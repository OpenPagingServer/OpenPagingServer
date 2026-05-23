HANDLER_NAME = "render_form"

def handle_request(*args, **kwargs):
    return getattr(module_web(), HANDLER_NAME)(*args, **kwargs)

def module_web():
    import importlib.util
    from pathlib import Path
    current = Path(__file__).resolve()
    module_dir = current.parents[1] if current.parent.name == "endpoint-forms" else current.parent
    spec = importlib.util.spec_from_file_location("siptrunks_web", module_dir / "web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

