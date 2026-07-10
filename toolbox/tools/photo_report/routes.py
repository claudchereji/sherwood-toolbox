"""HTTP layer for Photo Report. Builds the form, runs the headless
PhotoReportPDF generator on uploaded images, and streams the PDF back.

The PDF layout lives in restoration_common.PhotoReportPDF; this file only
collects inputs and wires them. Edit fields here; edit the PDF in
restoration_common.
"""
import io
import json
import os
import shutil
import tempfile

from flask import (Blueprint, jsonify, render_template, request, send_file)
from werkzeug.utils import secure_filename

from restoration_common import (PhotoReportPDF, get_company_by_id, load_companies,
                                 find_logo, generate_output_filename)

from ...core.crm import fetch_job_info

bp = Blueprint("photo_report", __name__, template_folder="templates",
               static_folder="static", static_url_path="static")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp"}


@bp.route("/")
def index():
    return render_template("photo_report.html", companies=load_companies())


@bp.route("/crm-fetch", methods=["POST"])
def crm_fetch():
    return jsonify(fetch_job_info(request.form.get("url", "")))


@bp.route("/generate", methods=["POST"])
def generate():
    form = request.form
    company = get_company_by_id(form.get("company_id", "")) or {}
    if not company:
        return jsonify({"error": "Select a company."}), 400

    job_info = {
        "customer_name": form.get("customer_name", "").strip(),
        "claim_number": form.get("claim_number", "").strip(),
        "job_location": form.get("job_location", "").strip(),
        "job_id": form.get("job_id", "").strip(),
        "photo_title": form.get("photo_title", "").strip() or "Photo Report",
        "photo_date": form.get("photo_date", "").strip(),
    }
    if not job_info["customer_name"]:
        return jsonify({"error": "Customer name is required."}), 400

    images = [f for f in request.files.getlist("photos")
              if f and os.path.splitext(f.filename)[1].lower() in IMAGE_EXTS]
    if not images:
        return jsonify({"error": "Select at least one image."}), 400

    try:
        max_size_mb = float(form.get("max_size_mb") or 10)
    except ValueError:
        max_size_mb = 10.0

    # Captions arrive as a JSON array parallel to the files list.
    try:
        captions = json.loads(form.get("photo_captions", "[]") or "[]")
    except (json.JSONDecodeError, TypeError):
        captions = []
    print(f"[photo_report] generate: company={company.get('id')}, "
          f"customer={job_info['customer_name']}, images={len(images)}, "
          f"captions={len(captions)}, caption_data={captions[:3]}")

    temp_dir = tempfile.mkdtemp(prefix="toolbox_photo_")
    try:
        image_paths = []
        for f in images:
            dest = os.path.join(temp_dir, secure_filename(f.filename))
            f.save(dest)
            image_paths.append(dest)

        logo_path = find_logo(temp_dir, company)
        out_path = os.path.join(temp_dir, "report.pdf")
        gen = PhotoReportPDF(out_path, job_info, company, logo_path=logo_path)

        # The generator's _create_page draws display_filename as the photo
        # info line. When a caption is provided, prepend it to the filename
        # so the PDF shows "Caption  |  filename.jpg" on each page.
        if captions and len(captions) == len(image_paths):
            display_names = []
            for i, path in enumerate(image_paths):
                cap = (captions[i] if i < len(captions) else "").strip()
                fname = os.path.basename(path)
                display_names.append(f"{cap}  |  {fname}" if cap else fname)
            print(f"[photo_report] captions applied: {display_names[:3]}")
            # Monkey-patch the display names into the generate flow by
            # overriding _create_page's default filename behavior.
            _orig_create_page = gen._create_page
            def _patched_create_page(c, image_path, display_filename=None,
                                     original_path=None, page_index=0):
                if display_filename is None and page_index < len(display_names):
                    display_filename = display_names[page_index]
                return _orig_create_page(c, image_path, display_filename,
                                         original_path, page_index)
            gen._create_page = _patched_create_page

        if not gen.generate(image_paths, max_size_mb=max_size_mb) or not os.path.exists(out_path):
            return jsonify({"error": "Could not generate the photo report."}), 500

        with open(out_path, "rb") as fh:
            data = io.BytesIO(fh.read())
        download_name = generate_output_filename(job_info)
        return send_file(data, mimetype="application/pdf",
                         as_attachment=True, download_name=download_name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
