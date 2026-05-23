import importlib.util

from srv.web.app import BASE_DIR


def handle_request():
    path = BASE_DIR / "srv" / "web" / "pages" / "admin" / "endpoint-action-page.py"
    spec = importlib.util.spec_from_file_location("web_admin_endpoint_action_page_delete", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.render_endpoint_action_page("delete")
