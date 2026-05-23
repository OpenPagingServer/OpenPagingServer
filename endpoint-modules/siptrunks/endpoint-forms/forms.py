
FORM_TYPE = "forms"


def module_web():
    import importlib.util
    from pathlib import Path

    current = Path(__file__).resolve()
    module_dir = current.parents[1] if current.parent.name == "endpoint-forms" else current.parent
    module_name = f"{module_dir.name.replace('-', '_')}_web"
    spec = importlib.util.spec_from_file_location(module_name, module_dir / "web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def forms():
    return module_web().forms()


def handle_request(request=None, conn_factory=None, page=None, user=None):
    if FORM_TYPE in {"forms", "index"}:
        return forms()
    return module_web().render_form(FORM_TYPE, request, conn_factory, page, user)