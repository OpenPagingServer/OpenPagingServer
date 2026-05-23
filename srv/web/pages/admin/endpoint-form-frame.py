from srv.web.app import *
import html
import io
import json
import os
import re
from contextlib import redirect_stdout
from pathlib import Path


def h(value):
    return html.escape("" if value is None else str(value), quote=True)


def frame_safe_name(value):
    return re.fullmatch(r"[A-Za-z0-9_-]+", str(value or "")) is not None


def frame_response(title, body, active="endpoints", user=None, status=200):
    return Response(
        f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{h(title)}</title></head>
<body>{body}</body>
</html>""",
        status=status,
        mimetype="text/html",
    )


def chooser_response(module, forms):
    style = "body{font-family:Tahoma,sans-serif;margin:0;padding:18px;color:#202124;background:#fff}.title{font-size:1.25em;font-weight:500;margin:0 0 14px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}.card{display:flex;flex-direction:column;gap:8px;min-height:112px;padding:16px;border:1px solid #ddd;border-radius:8px;text-decoration:none;color:inherit;background:#fff;box-shadow:0 2px 4px rgba(0,0,0,.08)}.card:hover,.card:focus{border-color:#1976D2;box-shadow:0 0 0 2px rgba(25,118,210,.15);outline:none}.name{font-weight:500}.desc{color:#555;line-height:1.4;font-size:.95em}@media(prefers-color-scheme:dark){body{background:#1e1e1e;color:#e0e0e0}.card{background:#171717;border-color:#333}.card:hover,.card:focus{border-color:#BB86FC;box-shadow:0 0 0 2px rgba(187,134,252,.18)}.desc{color:#bbb}}"
    body = '<h2 class="title">Endpoint type</h2><div class="grid">' + "".join(
        f"""<a class="card" href="/admin/endpoint-form-frame?{h(urlencode({"module": module, "type": key}))}">
            <span class="name">{h(form.get("label") or key)}</span>
            {f'<span class="desc">{h(form.get("description") or "")}</span>' if form.get("description") else ""}
        </a>"""
        for key, form in forms.items()
    ) + "</div>"
    return Response(
        f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>{style}</style></head><body>{body}</body></html>""",
        mimetype="text/html",
    )


def endpoint_frame_success_redirect(message):
    session["endpoint_flash_success"] = str(message)
    target = "/admin/manage-endpoints"
    return Response(
        f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Endpoint saved</title></head>
<body>
<script>
window.top.location.href = {json.dumps(target)};
</script>
<p>Endpoint saved. <a target="_top" href="{h(target)}">Return to Manage Endpoints</a>.</p>
</body>
</html>""",
        mimetype="text/html",
    )


def endpoint_frame_was_success(message, error, errors=None):
    message_text = str(message or "").strip()
    error_text = str(error or "").strip()
    has_errors = isinstance(errors, (list, tuple, dict, set)) and bool(errors)
    return request.method == "POST" and message_text != "" and error_text == "" and not has_errors


def run_py_file(path, extra=None):
    namespace = {}
    namespace.update(globals())
    namespace["__file__"] = str(path)
    namespace["__name__"] = "__endpoint_form__"
    if extra:
        namespace.update(extra)
    output = io.StringIO()
    with open(path, "r", encoding="utf-8-sig") as f:
        code = compile(f.read(), str(path), "exec")
    with redirect_stdout(output):
        exec(code, namespace)
    return namespace, output.getvalue()


def get_forms(registry_path):
    namespace, output = run_py_file(registry_path)
    if isinstance(namespace.get("FORMS"), dict):
        return namespace["FORMS"]
    if isinstance(namespace.get("forms"), dict):
        return namespace["forms"]
    if callable(namespace.get("forms")):
        result = namespace["forms"]()
        if isinstance(result, dict):
            return result
    return None


def response_from_output_or_result(output, result=None):
    if isinstance(result, Response):
        return result
    if isinstance(result, str):
        return Response(result, mimetype="text/html")
    if output:
        return Response(output, mimetype="text/html")
    return None


def call_handle_request(namespace, user, form_type):
    if not callable(namespace.get("handle_request")):
        return None
    try:
        return namespace["handle_request"](request, db, frame_response, user)
    except TypeError as first_error:
        try:
            return namespace["handle_request"](form_type, request, db, frame_response, user)
        except TypeError:
            try:
                return namespace["handle_request"]()
            except TypeError:
                raise first_error


def form_filename_from_entry(form_type, endpoint_form):
    raw = endpoint_form.get("file") if isinstance(endpoint_form, dict) else ""
    raw = str(raw or "").strip()
    if not raw:
        raw = form_type
    raw_path = Path(raw)
    raw_name = raw_path.name
    if raw_name != raw:
        return None
    stem = raw_path.stem if raw_path.suffix else raw_name
    if not frame_safe_name(stem):
        return None
    return f"{stem}.py"


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user

    module = request.args.get("module", "")
    form_type = request.args.get("type", "")

    if not frame_safe_name(module) or (form_type and not frame_safe_name(form_type)):
        return Response("Invalid endpoint form", status=400, mimetype="text/plain")

    root = BASE_DIR / "endpoint-modules"
    modules_root = root.resolve()

    module_dir = (modules_root / module).resolve()
    if modules_root not in module_dir.parents and module_dir != modules_root:
        return Response("Module not found", status=404, mimetype="text/plain")

    if not module_dir.is_dir():
        return Response("Module not found", status=404, mimetype="text/plain")

    forms_dir = (module_dir / "endpoint-forms").resolve()
    if not forms_dir.is_dir() or module_dir not in forms_dir.parents:
        return Response("Endpoint forms not found", status=404, mimetype="text/plain")

    registry_path = (forms_dir / "forms.py").resolve()
    if not registry_path.is_file() or forms_dir not in registry_path.parents:
        return Response("Module has no endpoint forms", status=404, mimetype="text/plain")

    forms = get_forms(registry_path)
    if not isinstance(forms, dict):
        return Response("Endpoint forms not found", status=404, mimetype="text/plain")

    if not form_type:
        index_path = (forms_dir / "index.py").resolve()
        if index_path.is_file() and forms_dir in index_path.parents:
            namespace, output = run_py_file(index_path, {"forms": forms, "module": module, "user": user})
            result = call_handle_request(namespace, user, form_type)
            response = response_from_output_or_result(output, result)
            if response is not None and not isinstance(result, dict):
                return response
        return chooser_response(module, forms)

    endpoint_form = forms.get(form_type)
    if not isinstance(endpoint_form, dict):
        return Response("Endpoint form not found", status=404, mimetype="text/plain")

    form_filename = form_filename_from_entry(form_type, endpoint_form)
    if not form_filename:
        return Response("Invalid endpoint form file", status=400, mimetype="text/plain")

    form_path = (forms_dir / form_filename).resolve()
    if not form_path.is_file() or forms_dir not in form_path.parents:
        return Response("Endpoint form file not found", status=404, mimetype="text/plain")

    namespace, output = run_py_file(
        form_path,
        {
            "endpoint_form": endpoint_form,
            "forms": forms,
            "module": module,
            "type": form_type,
            "user": user,
        },
    )

    result = call_handle_request(namespace, user, form_type)

    message = namespace.get("message", "")
    error = namespace.get("error", "")
    errors = namespace.get("errors", None)

    if endpoint_frame_was_success(message, error, errors):
        return endpoint_frame_success_redirect(message)

    response = response_from_output_or_result(output, result)
    return response if response is not None else Response("Endpoint form returned no response", status=500, mimetype="text/plain")
