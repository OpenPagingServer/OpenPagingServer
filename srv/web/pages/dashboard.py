from srv.web.app import *


def handle_request():
    user = require_user()
    if not isinstance(user, dict):
        return user

    ctx = legacy_user_context(user)
    content = f"""<h1>Hey there, <span id="extension-name">{h(ctx.get("username") or "User")}</span></h1>
<p>Thank you for trying the Open Paging Server Beta. In the future, this page will be used to show you currently active messages, and the ability to quickly trigger certain actions.</p>"""
    return legacy_page("Dashboard", ctx, "dashboard", "", content)
