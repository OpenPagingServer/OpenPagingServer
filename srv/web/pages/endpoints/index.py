from flask import abort, request

from srv.web.app import db, load_endpoint_web, safe_module_name


def handle_request():
    """Dispatch a public main-server route to an endpoint module's web hook.

    Endpoint modules opt in by defining::

        handle_web_request(path, request, conn_factory)

    The path is relative to ``/endpoints/<module>/`` and the return value may
    be anything Flask accepts as a view response. The hook owns authentication
    and authorization for its route; loading remains limited to trusted module
    packages by ``load_endpoint_web``.
    """
    view_args = request.view_args or {}
    module = str(view_args.get("module") or "")
    module_path = str(view_args.get("module_path") or "")
    if not safe_module_name(module):
        abort(404)

    web_module = load_endpoint_web(module)
    handler = getattr(web_module, "handle_web_request", None)
    if not callable(handler):
        abort(404)
    return handler(module_path, request, db)
