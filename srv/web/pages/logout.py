from srv.web.app import *

def handle_request():
    session.clear()
    return redirect("/index")
