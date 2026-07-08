from srv.web.app import *
from srv.web.pages.admin.settings.common import settings_page


ROLE_OPTIONS = [
    ("none", "None"),
    ("admin", "Administrator"),
    ("user", "User"),
    ("receiver", "Receiver"),
]
LDAP_ROLE_MAPPING_PERMISSION_SECTIONS = [
    ("Features", ["paging", "messages", "history", "bells", "assets"]),
    ("Message Administration", ["messages-add", "messages-edit", "messages-delete"]),
    ("Management", ["asset-edit", "groups-manage", "broadcasts-manage"]),
]


def _select_option_html(current, value, label):
    return f'<option value="{h(value)}"{" selected" if str(current) == str(value) else ""}>{h(label)}</option>'


def _valid_server_address(value):
    text = str(value or "").strip()
    if not text:
        return False
    if re.fullmatch(r"[A-Za-z0-9.-]+", text):
        return True
    return re.fullmatch(r"\[[0-9A-Fa-f:]+\]|[0-9A-Fa-f:.]+", text) is not None


def _clean_timeout(value, default="5"):
    text = str(value or "").strip() or str(default)
    try:
        number = int(text)
    except ValueError:
        return None
    if number < 1 or number > 120:
        return None
    return str(number)


def _clean_port(value, secure):
    fallback = "636" if secure else "389"
    text = str(value or "").strip() or fallback
    try:
        number = int(text)
    except ValueError:
        return None
    if number < 1 or number > 65535:
        return None
    return str(number)


def _valid_optional_url(value):
    text = str(value or "").strip()
    if not text:
        return True
    try:
        parsed = urlparse(text)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _group_option_rows():
    return query_all("SELECT id, name FROM `groups` ORDER BY name ASC, id ASC")


def _message_option_rows():
    return query_all("SELECT messageid, name FROM messages ORDER BY name ASC, messageid ASC")


def _bell_schedule_option_rows():
    ensure_bell_schema()
    return query_all("SELECT id, name FROM bell_schedules ORDER BY name ASC, id ASC")


def _normalize_login_settings(form, existing):
    existing_identity = identity_provider_settings(existing)
    group_rows = _group_option_rows()
    message_rows = _message_option_rows()
    bell_schedule_rows = _bell_schedule_option_rows()
    valid_group_ids = {str(row.get("id") or "").strip() for row in group_rows if str(row.get("id") or "").strip()}
    valid_message_ids = {str(row.get("messageid") or "").strip() for row in message_rows if str(row.get("messageid") or "").strip()}
    valid_schedule_ids = {str(row.get("id") or "").strip() for row in bell_schedule_rows if str(row.get("id") or "").strip()}
    provider = str(form.get("identity_provider") or "local").strip().lower()
    if provider not in IDENTITY_PROVIDER_VALUES:
        provider = "local"
    secure = form.get("ldap_secure") is not None
    bind_password = str(form.get("ldap_bind_password") or "")
    effective_bind_password = bind_password or str(existing_identity.get("ldap_bind_password") or "")
    oidc_client_secret = str(form.get("oidc_client_secret") or "").strip()
    effective_oidc_client_secret = oidc_client_secret or str(existing_identity.get("oidc_client_secret") or "").strip()
    oidc_scim_bearer_token = str(form.get("oidc_scim_bearer_token") or "").strip()
    effective_oidc_scim_bearer_token = oidc_scim_bearer_token or str(existing_identity.get("oidc_scim_bearer_token") or "").strip()
    saml_scim_bearer_token = str(form.get("saml_scim_bearer_token") or "").strip()
    effective_saml_scim_bearer_token = saml_scim_bearer_token or str(existing_identity.get("saml_scim_bearer_token") or "").strip()
    parsed_role_mappings = {}
    invalid_role_mapping_labels = []
    for setting_name, label in (
        (LDAP_ROLE_MAPPING_SETTING, "LDAP"),
        (OIDC_ROLE_MAPPING_SETTING, "OIDC"),
        (SAML_ROLE_MAPPING_SETTING, "SAML"),
    ):
        raw_value = str(form.get(setting_name) or "").strip()
        try:
            parsed_role_mappings[setting_name] = json.loads(raw_value) if raw_value else []
        except Exception:
            parsed_role_mappings[setting_name] = None
            invalid_role_mapping_labels.append(label)
    cleaned = {
        "login_banner_enabled": "1" if form.get("login_banner_enabled") else "0",
        "login_banner_title": str(form.get("login_banner_title") or ""),
        "login_banner_message": str(form.get("login_banner_message") or ""),
        "login_captcha_provider": str(form.get("login_captcha_provider") or "disabled").strip().lower(),
        "login_captcha_site_key": str(form.get("login_captcha_site_key") or "").strip(),
        "login_captcha_secret_key": "",
        "login_captcha_external_only": "1" if form.get("login_captcha_external_only") else "0",
        ACCOUNT_EXPIRATION_NOTIFY_SETTING: "1" if form.get(ACCOUNT_EXPIRATION_NOTIFY_SETTING) else "0",
        GUEST_RECEIVER_SETTING: "1" if form.get(GUEST_RECEIVER_SETTING) else "0",
        "identity_provider": provider,
        "identity_redirect_auto": "1" if form.get("identity_redirect_auto") else "0",
        "identity_allow_local_login": "1" if form.get("identity_allow_local_login") else "0",
        "ldap_enabled": "1" if provider == "ldap" else "0",
        "ldap_template": str(form.get("ldap_template") or "generic").strip().lower() or "generic",
        "ldap_server_address": str(form.get("ldap_server_address") or "").strip(),
        "ldap_server_port": str(form.get("ldap_server_port") or "").strip(),
        "ldap_secure": "1" if secure else "0",
        "ldap_ca_certificate": str(form.get("ldap_ca_certificate") or ""),
        "ldap_base_dn": str(form.get("ldap_base_dn") or "").strip(),
        "ldap_bind_username": str(form.get("ldap_bind_username") or "").strip(),
        "ldap_bind_password": effective_bind_password,
        "ldap_password_change_url": str(form.get("ldap_password_change_url") or "").strip(),
        "ldap_login_field": str(form.get("ldap_login_field") or "").strip(),
        "ldap_user_search_filter": str(form.get("ldap_user_search_filter") or "").strip(),
        "ldap_display_name_field": str(form.get("ldap_display_name_field") or "").strip(),
        "ldap_email_field": str(form.get("ldap_email_field") or "").strip(),
        "ldap_required_group": "",
        "ldap_admin_group": "",
        "ldap_auto_create_users": "0",
        "ldap_local_login_fallback": "1" if form.get("ldap_local_login_fallback") else "0",
        "ldap_connection_timeout": str(form.get("ldap_connection_timeout") or "").strip(),
        "ldap_failure_behavior": str(form.get("ldap_failure_behavior") or "deny").strip().lower(),
        "ldap_group_sync": "1",
        "ldap_default_role": "receiver",
        "oidc_discovery_url": str(form.get("oidc_discovery_url") or "").strip(),
        "oidc_client_id": str(form.get("oidc_client_id") or "").strip(),
        "oidc_client_secret": effective_oidc_client_secret,
        "oidc_password_change_url": str(form.get("oidc_password_change_url") or "").strip(),
        "oidc_scim_enabled": "1" if form.get("oidc_scim_enabled") else "0",
        "oidc_scim_base_url": str(form.get("oidc_scim_base_url") or "").strip(),
        "oidc_scim_bearer_token": effective_oidc_scim_bearer_token,
        "oidc_scim_timeout": str(form.get("oidc_scim_timeout") or "").strip(),
        "oidc_scim_sync_groups": "1",
        "oidc_scope": str(form.get("oidc_scope") or OIDC_SETTING_DEFAULTS["oidc_scope"]).strip() or OIDC_SETTING_DEFAULTS["oidc_scope"],
        "oidc_username_claim": str(form.get("oidc_username_claim") or OIDC_SETTING_DEFAULTS["oidc_username_claim"]).strip() or OIDC_SETTING_DEFAULTS["oidc_username_claim"],
        "oidc_display_name_claim": str(form.get("oidc_display_name_claim") or OIDC_SETTING_DEFAULTS["oidc_display_name_claim"]).strip() or OIDC_SETTING_DEFAULTS["oidc_display_name_claim"],
        "oidc_email_claim": str(form.get("oidc_email_claim") or OIDC_SETTING_DEFAULTS["oidc_email_claim"]).strip() or OIDC_SETTING_DEFAULTS["oidc_email_claim"],
        "oidc_groups_claim": str(form.get("oidc_groups_claim") or OIDC_SETTING_DEFAULTS["oidc_groups_claim"]).strip() or OIDC_SETTING_DEFAULTS["oidc_groups_claim"],
        "oidc_required_group": "",
        "oidc_admin_group": "",
        "oidc_auto_create_users": "0",
        "oidc_group_sync": "1",
        "oidc_default_role": "receiver",
        OIDC_ROLE_MAPPING_SETTING: normalize_ldap_role_mappings(parsed_role_mappings.get(OIDC_ROLE_MAPPING_SETTING) or [], valid_group_ids, valid_message_ids, valid_schedule_ids),
        "saml_idp_entity_id": str(form.get("saml_idp_entity_id") or "").strip(),
        "saml_sso_url": str(form.get("saml_sso_url") or "").strip(),
        "saml_x509_certificate": str(form.get("saml_x509_certificate") or "").strip(),
        "saml_password_change_url": str(form.get("saml_password_change_url") or "").strip(),
        "saml_scim_enabled": "1" if form.get("saml_scim_enabled") else "0",
        "saml_scim_base_url": str(form.get("saml_scim_base_url") or "").strip(),
        "saml_scim_bearer_token": effective_saml_scim_bearer_token,
        "saml_scim_timeout": str(form.get("saml_scim_timeout") or "").strip(),
        "saml_scim_sync_groups": "1",
        "saml_username_attribute": str(form.get("saml_username_attribute") or SAML_SETTING_DEFAULTS["saml_username_attribute"]).strip() or SAML_SETTING_DEFAULTS["saml_username_attribute"],
        "saml_display_name_attribute": str(form.get("saml_display_name_attribute") or SAML_SETTING_DEFAULTS["saml_display_name_attribute"]).strip() or SAML_SETTING_DEFAULTS["saml_display_name_attribute"],
        "saml_email_attribute": str(form.get("saml_email_attribute") or SAML_SETTING_DEFAULTS["saml_email_attribute"]).strip() or SAML_SETTING_DEFAULTS["saml_email_attribute"],
        "saml_groups_attribute": str(form.get("saml_groups_attribute") or SAML_SETTING_DEFAULTS["saml_groups_attribute"]).strip() or SAML_SETTING_DEFAULTS["saml_groups_attribute"],
        "saml_required_group": "",
        "saml_admin_group": "",
        "saml_auto_create_users": "0",
        "saml_group_sync": "1",
        "saml_default_role": "receiver",
        SAML_ROLE_MAPPING_SETTING: normalize_ldap_role_mappings(parsed_role_mappings.get(SAML_ROLE_MAPPING_SETTING) or [], valid_group_ids, valid_message_ids, valid_schedule_ids),
        LDAP_ROLE_MAPPING_SETTING: normalize_ldap_role_mappings(parsed_role_mappings.get(LDAP_ROLE_MAPPING_SETTING) or [], valid_group_ids, valid_message_ids, valid_schedule_ids),
    }
    errors = []

    if invalid_role_mapping_labels:
        errors.append(f"{', '.join(invalid_role_mapping_labels)} role mapping data is invalid.")

    valid_providers = {"disabled", "basic", "turnstile", "recaptcha"}
    if cleaned["login_captcha_provider"] not in valid_providers:
        errors.append("Select a valid CAPTCHA provider.")
        cleaned["login_captcha_provider"] = "disabled"
    existing_secret = str(existing.get("login_captcha_secret_key") or "").strip()
    cleaned["login_captcha_secret_key"] = (
        str(form.get("login_captcha_secret_key") or "").strip() or existing_secret
    ) if cleaned["login_captcha_provider"] in {"turnstile", "recaptcha"} else ""
    if cleaned["login_captcha_provider"] in {"turnstile", "recaptcha"} and (
        not cleaned["login_captcha_site_key"] or not cleaned["login_captcha_secret_key"]
    ):
        errors.append("Site key and secret key are required for the selected CAPTCHA provider.")

    if cleaned["ldap_failure_behavior"] not in IDENTITY_FAILURE_BEHAVIORS:
        errors.append("Select a valid failure behavior.")
        cleaned["ldap_failure_behavior"] = "deny"
    for field_name, label in (
        ("ldap_password_change_url", "LDAP password change URL"),
        ("oidc_password_change_url", "OIDC password change URL"),
        ("saml_password_change_url", "SAML password change URL"),
    ):
        if not _valid_optional_url(cleaned.get(field_name)):
            errors.append(f"{label} must be blank or a valid HTTP/HTTPS URL.")
    timeout_value = _clean_timeout(cleaned["ldap_connection_timeout"])
    if timeout_value is None:
        errors.append("Connection timeout must be between 1 and 120 seconds.")
    else:
        cleaned["ldap_connection_timeout"] = timeout_value
    for field_name, label in (
        ("oidc_scim_timeout", "OIDC SCIM timeout"),
        ("saml_scim_timeout", "SAML SCIM timeout"),
    ):
        timeout_value = _clean_timeout(cleaned[field_name])
        if timeout_value is None:
            errors.append(f"{label} must be between 1 and 120 seconds.")
        else:
            cleaned[field_name] = timeout_value
    port_value = _clean_port(cleaned["ldap_server_port"], secure)
    if port_value is None:
        errors.append("Server port must be between 1 and 65535.")
    else:
        cleaned["ldap_server_port"] = port_value

    if provider == "ldap":
        if Connection is None or Server is None:
            errors.append("LDAP Python library not installed in running environment")
            return cleaned, errors
        if not cleaned["ldap_server_address"] or not _valid_server_address(cleaned["ldap_server_address"]):
            errors.append("Server address is required.")
        if not cleaned["ldap_base_dn"]:
            errors.append("Base DN is required.")
        if cleaned["ldap_bind_username"] and not cleaned["ldap_bind_password"]:
            errors.append("Bind password is required when bind username is set.")
        if not cleaned["ldap_login_field"]:
            errors.append("Login field is required.")
        if not cleaned["ldap_user_search_filter"]:
            errors.append("User search filter is required.")
        elif "{username}" not in cleaned["ldap_user_search_filter"] and "{login}" not in cleaned["ldap_user_search_filter"]:
            errors.append("User search filter must include {username} or {login}.")
        if not cleaned["ldap_display_name_field"]:
            errors.append("Display name field is required.")
        if not cleaned["ldap_email_field"]:
            errors.append("Email field is required.")
        ca_value = str(cleaned["ldap_ca_certificate"] or "").strip()
        if ca_value and "BEGIN CERTIFICATE" not in ca_value and not Path(ca_value).is_file():
            errors.append("CA certificate must be blank, a file path, or PEM text.")
    elif provider == "oidc":
        if OAuth is None:
            errors.append("OIDC Python library not installed in running environment")
            return cleaned, errors
        if not cleaned["oidc_discovery_url"]:
            errors.append("OIDC discovery URL is required.")
        if not cleaned["oidc_client_id"]:
            errors.append("OIDC client ID is required.")
        if not cleaned["oidc_client_secret"]:
            errors.append("OIDC client secret is required.")
        if not cleaned["oidc_username_claim"]:
            errors.append("OIDC username claim is required.")
        if not cleaned["oidc_email_claim"]:
            errors.append("OIDC email claim is required.")
    elif provider == "saml":
        if OneLogin_Saml2_Auth is None:
            errors.append("SAML Python library not installed in running environment")
            return cleaned, errors
        if not cleaned["saml_idp_entity_id"]:
            errors.append("SAML IdP entity ID is required.")
        if not cleaned["saml_sso_url"]:
            errors.append("SAML SSO URL is required.")
        if not cleaned["saml_x509_certificate"]:
            errors.append("SAML X.509 certificate is required.")
        if not cleaned["saml_username_attribute"]:
            errors.append("SAML username attribute is required.")
        if not cleaned["saml_email_attribute"]:
            errors.append("SAML email attribute is required.")

    for prefix, label in (("oidc", "OIDC"), ("saml", "SAML")):
        if cleaned.get(f"{prefix}_scim_enabled") != "1":
            continue
        if not _valid_optional_url(cleaned.get(f"{prefix}_scim_base_url")):
            errors.append(f"{label} SCIM base URL must be a valid HTTP/HTTPS URL.")
        elif not cleaned.get(f"{prefix}_scim_base_url"):
            errors.append(f"{label} SCIM base URL is required.")
        if not cleaned.get(f"{prefix}_scim_bearer_token"):
            errors.append(f"{label} SCIM bearer token is required.")

    return cleaned, errors


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    identity_data = identity_provider_settings(data)
    if request.method == "POST":
        if demo_mode_enabled():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message="Demo Mode is enabled."), 403
            return demo_mode_page("Login Settings", legacy_user_context(user), "settings", "settings")
        cleaned, errors = _normalize_login_settings(request.form, data)
        if errors:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message=" ".join(errors))
            return page("Login Settings", h(" ".join(errors)), "settings", user)
        save_setting("login_banner_enabled", cleaned["login_banner_enabled"], "Enable login banner")
        save_setting("login_banner_title", cleaned["login_banner_title"], "Login banner title")
        save_setting("login_banner_message", cleaned["login_banner_message"], "Login banner message")
        save_setting("login_captcha_provider", cleaned["login_captcha_provider"], "Login CAPTCHA provider")
        save_setting("login_captcha_site_key", cleaned["login_captcha_site_key"], "Login CAPTCHA site key")
        save_setting("login_captcha_secret_key", cleaned["login_captcha_secret_key"], "Login CAPTCHA secret key")
        save_setting("login_captcha_external_only", cleaned["login_captcha_external_only"], "Require login CAPTCHA only for external IP addresses (0/1)")
        save_setting(ACCOUNT_EXPIRATION_NOTIFY_SETTING, cleaned[ACCOUNT_EXPIRATION_NOTIFY_SETTING], "Notify users whose accounts are about to expire (0/1)")
        save_setting(GUEST_RECEIVER_SETTING, cleaned[GUEST_RECEIVER_SETTING], "Enable guest receiver (0/1)")
        save_setting("identity_provider", cleaned["identity_provider"], "Identity provider")
        save_setting("identity_redirect_auto", cleaned["identity_redirect_auto"], "Redirect login page to provider automatically (0/1)")
        save_setting("identity_allow_local_login", cleaned["identity_allow_local_login"], "Always allow local login (0/1)")
        save_setting("ldap_enabled", cleaned["ldap_enabled"], "LDAP enabled (0/1)")
        save_setting("ldap_template", cleaned["ldap_template"], "LDAP template")
        save_setting("ldap_server_address", cleaned["ldap_server_address"], "LDAP server address")
        save_setting("ldap_server_port", cleaned["ldap_server_port"], "LDAP server port")
        save_setting("ldap_secure", cleaned["ldap_secure"], "Secure LDAP (0/1)")
        save_setting("ldap_ca_certificate", cleaned["ldap_ca_certificate"], "LDAP CA certificate")
        save_setting("ldap_base_dn", cleaned["ldap_base_dn"], "LDAP base DN")
        save_setting("ldap_bind_username", cleaned["ldap_bind_username"], "LDAP bind username")
        save_setting("ldap_bind_password", cleaned["ldap_bind_password"], "LDAP bind password")
        save_setting("ldap_password_change_url", cleaned["ldap_password_change_url"], "LDAP password change URL")
        save_setting("ldap_login_field", cleaned["ldap_login_field"], "LDAP login field")
        save_setting("ldap_user_search_filter", cleaned["ldap_user_search_filter"], "LDAP user search filter")
        save_setting("ldap_display_name_field", cleaned["ldap_display_name_field"], "LDAP display name field")
        save_setting("ldap_email_field", cleaned["ldap_email_field"], "LDAP email field")
        save_setting("ldap_required_group", cleaned["ldap_required_group"], "LDAP required group")
        save_setting("ldap_admin_group", cleaned["ldap_admin_group"], "LDAP admin group")
        save_setting("ldap_auto_create_users", cleaned["ldap_auto_create_users"], "LDAP auto-create users (0/1)")
        save_setting("ldap_local_login_fallback", cleaned["ldap_local_login_fallback"], "LDAP local login fallback (0/1)")
        save_setting("ldap_connection_timeout", cleaned["ldap_connection_timeout"], "LDAP connection timeout")
        save_setting("ldap_failure_behavior", cleaned["ldap_failure_behavior"], "LDAP failure behavior")
        save_setting("ldap_group_sync", cleaned["ldap_group_sync"], "LDAP group sync (0/1)")
        save_setting("ldap_default_role", cleaned["ldap_default_role"], "LDAP default role")
        save_setting("oidc_discovery_url", cleaned["oidc_discovery_url"], "OIDC discovery URL")
        save_setting("oidc_client_id", cleaned["oidc_client_id"], "OIDC client ID")
        save_setting("oidc_client_secret", cleaned["oidc_client_secret"], "OIDC client secret")
        save_setting("oidc_password_change_url", cleaned["oidc_password_change_url"], "OIDC password change URL")
        save_setting("oidc_scim_enabled", cleaned["oidc_scim_enabled"], "OIDC SCIM enabled (0/1)")
        save_setting("oidc_scim_base_url", cleaned["oidc_scim_base_url"], "OIDC SCIM base URL")
        save_setting("oidc_scim_bearer_token", cleaned["oidc_scim_bearer_token"], "OIDC SCIM bearer token")
        save_setting("oidc_scim_timeout", cleaned["oidc_scim_timeout"], "OIDC SCIM timeout")
        save_setting("oidc_scim_sync_groups", cleaned["oidc_scim_sync_groups"], "OIDC SCIM group sync (0/1)")
        save_setting("oidc_scope", cleaned["oidc_scope"], "OIDC scope")
        save_setting("oidc_username_claim", cleaned["oidc_username_claim"], "OIDC username claim")
        save_setting("oidc_display_name_claim", cleaned["oidc_display_name_claim"], "OIDC display name claim")
        save_setting("oidc_email_claim", cleaned["oidc_email_claim"], "OIDC email claim")
        save_setting("oidc_groups_claim", cleaned["oidc_groups_claim"], "OIDC groups claim")
        save_setting("oidc_required_group", cleaned["oidc_required_group"], "OIDC required group")
        save_setting("oidc_admin_group", cleaned["oidc_admin_group"], "OIDC admin group")
        save_setting("oidc_auto_create_users", cleaned["oidc_auto_create_users"], "OIDC auto-create users (0/1)")
        save_setting("oidc_group_sync", cleaned["oidc_group_sync"], "OIDC group sync (0/1)")
        save_setting("oidc_default_role", cleaned["oidc_default_role"], "OIDC default role")
        save_setting(OIDC_ROLE_MAPPING_SETTING, json.dumps(cleaned[OIDC_ROLE_MAPPING_SETTING], separators=(",", ":")), "OIDC role mappings")
        save_setting("saml_idp_entity_id", cleaned["saml_idp_entity_id"], "SAML IdP entity ID")
        save_setting("saml_sso_url", cleaned["saml_sso_url"], "SAML SSO URL")
        save_setting("saml_x509_certificate", cleaned["saml_x509_certificate"], "SAML X.509 certificate")
        save_setting("saml_password_change_url", cleaned["saml_password_change_url"], "SAML password change URL")
        save_setting("saml_scim_enabled", cleaned["saml_scim_enabled"], "SAML SCIM enabled (0/1)")
        save_setting("saml_scim_base_url", cleaned["saml_scim_base_url"], "SAML SCIM base URL")
        save_setting("saml_scim_bearer_token", cleaned["saml_scim_bearer_token"], "SAML SCIM bearer token")
        save_setting("saml_scim_timeout", cleaned["saml_scim_timeout"], "SAML SCIM timeout")
        save_setting("saml_scim_sync_groups", cleaned["saml_scim_sync_groups"], "SAML SCIM group sync (0/1)")
        save_setting("saml_username_attribute", cleaned["saml_username_attribute"], "SAML username attribute")
        save_setting("saml_display_name_attribute", cleaned["saml_display_name_attribute"], "SAML display name attribute")
        save_setting("saml_email_attribute", cleaned["saml_email_attribute"], "SAML email attribute")
        save_setting("saml_groups_attribute", cleaned["saml_groups_attribute"], "SAML groups attribute")
        save_setting("saml_required_group", cleaned["saml_required_group"], "SAML required group")
        save_setting("saml_admin_group", cleaned["saml_admin_group"], "SAML admin group")
        save_setting("saml_auto_create_users", cleaned["saml_auto_create_users"], "SAML auto-create users (0/1)")
        save_setting("saml_group_sync", cleaned["saml_group_sync"], "SAML group sync (0/1)")
        save_setting("saml_default_role", cleaned["saml_default_role"], "SAML default role")
        save_setting(SAML_ROLE_MAPPING_SETTING, json.dumps(cleaned[SAML_ROLE_MAPPING_SETTING], separators=(",", ":")), "SAML role mappings")
        save_setting(LDAP_ROLE_MAPPING_SETTING, json.dumps(cleaned[LDAP_ROLE_MAPPING_SETTING], separators=(",", ":")), "LDAP role mappings")
        ensure_scim_reconcile_thread()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(status="success")
        return redirect("/admin/settings/login")

    ctx = legacy_user_context(user)
    banner_enabled = data.get("login_banner_enabled", "0") == "1"
    banner_disabled = "" if banner_enabled else " disabled"
    captcha_provider = str(data.get("login_captcha_provider") or "disabled").strip().lower()
    if captcha_provider not in {"disabled", "basic", "turnstile", "recaptcha"}:
        captcha_provider = "disabled"
    provider = identity_data.get("identity_provider", "local")
    group_rows = _group_option_rows()
    message_rows = _message_option_rows()
    bell_schedule_rows = _bell_schedule_option_rows()
    role_mapping_state = identity_data.get("ldap_role_mappings") or []
    oidc_role_mapping_state = identity_data.get(OIDC_ROLE_MAPPING_SETTING) or []
    saml_role_mapping_state = identity_data.get(SAML_ROLE_MAPPING_SETTING) or []
    role_mapping_group_options = [
        {"id": str(row.get("id") or "").strip(), "label": str(row.get("name") or row.get("id") or "").strip()}
        for row in group_rows
        if str(row.get("id") or "").strip()
    ]
    role_mapping_message_options = [
        {"id": str(row.get("messageid") or "").strip(), "label": str(row.get("name") or row.get("messageid") or "").strip()}
        for row in message_rows
        if str(row.get("messageid") or "").strip()
    ]
    role_mapping_bell_schedule_options = [
        {"id": str(row.get("id") or "").strip(), "label": str(row.get("name") or row.get("id") or "").strip()}
        for row in bell_schedule_rows
        if str(row.get("id") or "").strip()
    ]
    role_mapping_permission_sections = [
        {"title": title, "values": values}
        for title, values in LDAP_ROLE_MAPPING_PERMISSION_SECTIONS
    ]
    default_user_permission_values = permission_selected_values(DEFAULT_USER_PAGE_PERMISSIONS.get("user", set()), USER_PERMISSION_LABELS)

    ldap_role_options_html = "".join(
        _select_option_html(normalize_ldap_default_role(identity_data.get("ldap_default_role", "receiver")), value, label)
        for value, label in ROLE_OPTIONS
    )
    oidc_role_options_html = "".join(
        _select_option_html(normalize_ldap_default_role(identity_data.get("oidc_default_role", "receiver")), value, label)
        for value, label in ROLE_OPTIONS
    )
    saml_role_options_html = "".join(
        _select_option_html(normalize_ldap_default_role(identity_data.get("saml_default_role", "receiver")), value, label)
        for value, label in ROLE_OPTIONS
    )
    captcha_options = "".join(
        _select_option_html(captcha_provider, value, label)
        for value, label in (
            ("disabled", "Disabled"),
            ("basic", "Basic Captcha"),
            ("turnstile", "Cloudflare Turnstile"),
            ("recaptcha", "Google reCAPTCHA"),
        )
    )
    provider_options = "".join(
        _select_option_html(provider, value, label)
        for value, label in (("local", "Local"), ("ldap", "LDAP"), ("oidc", "OIDC"), ("saml", "SAML"))
    )
    failure_options = "".join(
        _select_option_html(identity_data.get("ldap_failure_behavior", "deny"), value, label)
        for value, label in (("deny", "Deny"), ("fallback", "Local Fallback"))
    )
    body = f"""
    <style>
        .ldap-settings-dropdown {{
            margin-top: 8px;
            border: 1px solid #E6E6E6;
            border-radius: 8px;
            padding: 0;
            overflow: visible;
        }}
        .ldap-settings-dropdown summary,
        .ldap-settings-header {{
            list-style: none;
            cursor: pointer;
            padding: 14px 16px;
            font-weight: 500;
            color: #5F6368;
            display: flex;
            align-items: center;
            justify-content: space-between;
            user-select: none;
        }}
        .ldap-settings-header {{
            cursor: default;
        }}
        .ldap-settings-dropdown summary::-webkit-details-marker {{
            display: none;
        }}
        .ldap-settings-dropdown summary::after {{
            content: '\\25BE';
            font-size: 0.85em;
            transition: transform 0.2s ease;
        }}
        .ldap-settings-dropdown[open] summary::after {{
            transform: rotate(180deg);
        }}
        .ldap-settings-panel {{
            padding: 0 16px 16px 16px;
        }}
        .ldap-settings-dropdown.nested-identity-settings {{
            margin-top: 12px;
            background: #FFF;
        }}
        .scim-settings-fields .info-row:last-child {{
            padding-bottom: 0;
        }}
        .ldap-settings-panel .info-row {{
            border-bottom: none;
            padding: 0 0 14px 0;
        }}
        .ldap-settings-panel .info-row:last-child {{
            padding-bottom: 0;
        }}
        .settings-section-divider {{
            border-bottom: 1px solid #333;
            padding-bottom: 16px;
            margin-bottom: 20px;
        }}
        .ldap-role-mapping-list {{
            display: flex;
            flex-direction: column;
            gap: 12px;
            width: 100%;
        }}
        .ldap-role-mapping-card {{
            border: 1px solid #D8DEE6;
            border-radius: 12px;
            background: #F8F9FA;
            padding: 12px;
        }}
        .ldap-role-mapping-head {{
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr) minmax(0, 1.4fr) 180px auto;
            gap: 10px;
            align-items: start;
        }}
        .ldap-role-mapping-recipient-list {{
            display: grid;
            gap: 8px;
            max-height: 180px;
            overflow: auto;
            padding-right: 4px;
        }}
        .ldap-role-mapping-recipient-list .ldap-role-empty {{
            padding-top: 0;
        }}
        .ldap-role-mapping-recipient-dropdown {{
            position: relative;
        }}
        .login-settings button.ldap-role-mapping-recipient-trigger {{
            width: 100%;
            min-height: 42px;
            border: 1px solid #CCC;
            border-radius: 6px;
            background: #FFF;
            color: #222;
            padding: 10px;
            font-size: 14px;
            font-weight: 300;
            text-align: left;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            cursor: pointer;
        }}
        .login-settings button.ldap-role-mapping-recipient-trigger:hover {{
            background: #FFF;
        }}
        .ldap-role-mapping-recipient-trigger .summary {{
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .ldap-role-mapping-recipient-trigger .caret {{
            color: #666;
            font-size: 0.85em;
        }}
        .ldap-role-mapping-recipient-dropdown.open .ldap-role-mapping-recipient-trigger .caret {{
            transform: rotate(180deg);
        }}
        .ldap-role-mapping-recipient-menu {{
            display: none;
            position: absolute;
            top: calc(100% + 6px);
            left: 0;
            right: 0;
            z-index: 20;
            border: 1px solid #DADCE0;
            border-radius: 10px;
            background: #FFF;
            box-shadow: 0 8px 18px rgba(0,0,0,0.15);
            padding: 10px;
        }}
        .ldap-role-mapping-recipient-dropdown.open .ldap-role-mapping-recipient-menu {{
            display: block;
        }}
        .ldap-role-mapping-field {{
            display: flex;
            flex-direction: column;
            gap: 8px;
            min-width: 0;
        }}
        .ldap-role-mapping-field-fallback {{
            grid-column: span 2;
            align-self: stretch;
            justify-content: center;
        }}
        .ldap-role-mapping-fallback-text {{
            color: #5F6368;
            font-weight: 500;
        }}
        .ldap-role-mapping-remove {{
            align-self: end;
            background: #F1F3F4;
            color: #5F6368;
            border: 1px solid #DADCE0;
        }}
        .ldap-role-mapping-remove:hover {{
            background: #E8EAED;
            color: #3C4043;
        }}
        .ldap-role-mapping-add {{
            margin-top: 0;
        }}
        .ldap-role-user-permissions {{
            margin-top: 12px;
            border: 1px solid #DADCE0;
            border-radius: 12px;
            background: #EEEFF1;
            overflow: hidden;
        }}
        .ldap-role-user-permissions summary {{
            list-style: none;
            cursor: pointer;
            padding: 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            color: #5F6368;
            font-size: 1.02em;
            font-weight: 500;
            user-select: none;
        }}
        .ldap-role-user-permissions summary::-webkit-details-marker {{
            display: none;
        }}
        .ldap-role-user-permissions summary i {{
            transition: transform 0.2s ease;
            color: #5F6368;
        }}
        .ldap-role-user-permissions[open] summary i {{
            transform: rotate(180deg);
        }}
        .ldap-role-user-permissions-body {{
            padding: 0 12px 12px 12px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .ldap-role-permission-stack {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .ldap-role-permission-block {{
            border: 1px solid #D8DEE6;
            border-radius: 12px;
            background: #F8F9FA;
            padding: 12px;
        }}
        .ldap-role-permission-title {{
            font-size: 0.82em;
            font-weight: 600;
            letter-spacing: .04em;
            text-transform: uppercase;
            color: #5F6368;
            margin-bottom: 10px;
        }}
        .ldap-role-permission-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 10px;
        }}
        .ldap-role-permission-choice {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            padding: 10px 12px;
            border: 1px solid #DADCE0;
            border-radius: 10px;
            background: #FFF;
            color: #3C4043;
        }}
        .ldap-role-scope-panel {{
            display: grid;
            gap: 12px;
        }}
        .ldap-role-scope-box {{
            border: 1px solid #D8DEE6;
            border-radius: 12px;
            padding: 12px;
            background: #F8F9FA;
        }}
        .ldap-role-scope-toggle {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
        }}
        .ldap-role-scope-list {{
            display: grid;
            gap: 8px;
            max-height: 220px;
            overflow: auto;
            padding-right: 4px;
            margin-top: 12px;
        }}
        .ldap-role-scope-choice {{
            display: flex;
            align-items: flex-start;
            gap: 12px;
            color: #3C4043;
            padding: 8px 10px;
            border: 1px solid #DADCE0;
            border-radius: 10px;
            background: #FFF;
        }}
        .ldap-role-empty {{
            color: #777;
            font-size: 0.9em;
            padding: 10px 0 2px 0;
        }}
        @media (max-width: 767px) {{
            .ldap-role-mapping-head {{
                grid-template-columns: 1fr;
            }}
            .ldap-role-mapping-field-fallback {{
                grid-column: auto;
            }}
            .ldap-role-mapping-remove {{
                width: 100%;
            }}
        }}
        @media(prefers-color-scheme:dark) {{
            .ldap-settings-dropdown {{
                border-color: #333;
            }}
            .ldap-settings-dropdown summary {{
                color: #E0E0E0;
            }}
            .ldap-settings-dropdown.nested-identity-settings {{
                background: #1B1D20;
            }}
            .settings-section-divider {{
                border-bottom-color: #333;
            }}
            .ldap-role-mapping-card, .ldap-role-permission-block, .ldap-role-scope-box {{
                border-color: #333;
                background: #1B1D20;
            }}
            .login-settings button.ldap-role-mapping-recipient-trigger {{
                border-color: #444;
                background: #1E1E1E;
                color: #E0E0E0;
            }}
            .login-settings button.ldap-role-mapping-recipient-trigger:hover {{
                background: #1E1E1E;
            }}
            .ldap-role-mapping-recipient-trigger .caret {{
                color: #BBB;
            }}
            .ldap-role-mapping-recipient-menu {{
                border-color: #3C4043;
                background: #1B1D20;
                box-shadow: 0 10px 24px rgba(0,0,0,0.45);
            }}
            .ldap-role-mapping-fallback-text {{
                color: #BBB;
            }}
            .ldap-role-user-permissions {{
                border-color: #333;
                background: #1B1D20;
            }}
            .ldap-role-user-permissions summary, .ldap-role-user-permissions summary i, .ldap-role-permission-title, .ldap-settings-header {{
                color: #E0E0E0;
            }}
            .ldap-role-permission-choice, .ldap-role-scope-choice {{
                border-color: #3C4043;
                background: #202124;
                color: #E0E0E0;
            }}
            .ldap-role-empty {{
                color: #BBB;
            }}
            .ldap-role-mapping-remove {{
                background: #252525;
                border-color: #444;
                color: #E0E0E0;
            }}
            .ldap-role-mapping-remove:hover {{
                background: #303030;
                color: #FFF;
            }}
        }}
    </style>
    <div id="login" class="tab-content active">
        <div class="info-card login-settings">
            <form id="loginSettingsForm">
                <h4>Identity Provider</h4>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none; padding-bottom: 0;">
                    <select name="identity_provider" id="identityProvider">
                        {provider_options}
                    </select>
                </div>
                <div class="settings-section-divider">
                    <div id="localIdentityNotice" style="margin-top:12px; margin-bottom:16px;">
                        <p>All users are managed and stored on this server. System administrators are responsible for adding and removing each individual access when necessary.</p>
                    </div>
                    <div id="ssoIdentityOptions" style="display:none; margin-top:12px; margin-bottom:16px;">
                        <div class="info-row" style="border-bottom:none;">
                            <span class="info-label">Redirect login page to provider automatically</span>
                            <span><label class="switch"><input type="checkbox" name="identity_redirect_auto" id="identityRedirectAuto"{" checked" if identity_data.get("identity_redirect_auto") == "1" else ""}><span class="slider"></span></label></span>
                        </div>
                        <div class="info-row" style="border-bottom:none;">
                            <span class="info-label">Always allow local login</span>
                            <span><label class="switch"><input type="checkbox" name="identity_allow_local_login" id="identityAllowLocalLogin"{" checked" if identity_data.get("identity_allow_local_login") == "1" else ""}><span class="slider"></span></label></span>
                        </div>
                    </div>
                    <div id="ldapIdentityFields" style="display:none;">
                        <details class="ldap-settings-dropdown" id="ldapSettingsDropdown">
                            <summary>LDAP Settings</summary>
                            <div class="ldap-settings-panel">
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <input type="hidden" name="ldap_template" id="ldapTemplate" value="{h(identity_data.get("ldap_template", "generic"))}">
                                    <select id="ldapTemplatePicker">
                                        <option value="" selected hidden>Template</option>
                                        <option value="generic">Generic LDAP</option>
                                        <option value="active-directory">Active Directory</option>
                                    </select>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Server address</span>
                                    <input type="text" name="ldap_server_address" id="ldapServerAddress" value="{h(identity_data.get("ldap_server_address", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Server port</span>
                                    <input type="number" name="ldap_server_port" id="ldapServerPort" min="1" max="65535" value="{h(identity_data.get("ldap_server_port", "389"))}">
                                </div>
                                <div class="info-row">
                                    <span class="info-label">Secure LDAP</span>
                                    <span><label class="switch"><input type="checkbox" name="ldap_secure" id="ldapSecure"{" checked" if identity_data.get("ldap_secure") == "1" else ""}><span class="slider"></span></label></span>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">CA certificate</span>
                                    <textarea name="ldap_ca_certificate" id="ldapCaCertificate">{h(identity_data.get("ldap_ca_certificate", ""))}</textarea>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Base DN</span>
                                    <input type="text" name="ldap_base_dn" id="ldapBaseDn" value="{h(identity_data.get("ldap_base_dn", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Bind username</span>
                                    <input type="text" name="ldap_bind_username" id="ldapBindUsername" value="{h(identity_data.get("ldap_bind_username", ""))}" autocomplete="off">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Bind password</span>
                                    <input type="password" name="ldap_bind_password" id="ldapBindPassword" value="" autocomplete="off" placeholder="Leave blank to keep current bind password">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Password change URL</span>
                                    <input type="text" name="ldap_password_change_url" id="ldapPasswordChangeUrl" value="{h(identity_data.get("ldap_password_change_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Login field</span>
                                    <input type="text" name="ldap_login_field" id="ldapLoginField" value="{h(identity_data.get("ldap_login_field", "uid"))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">User search filter</span>
                                    <input type="text" name="ldap_user_search_filter" id="ldapUserSearchFilter" value="{h(identity_data.get("ldap_user_search_filter", "({field}={username})"))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Display name field</span>
                                    <input type="text" name="ldap_display_name_field" id="ldapDisplayNameField" value="{h(identity_data.get("ldap_display_name_field", "cn"))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Email field</span>
                                    <input type="text" name="ldap_email_field" id="ldapEmailField" value="{h(identity_data.get("ldap_email_field", "mail"))}">
                                </div>
                                <div class="info-row">
                                    <span class="info-label">Local login fallback</span>
                                    <span><label class="switch"><input type="checkbox" name="ldap_local_login_fallback" id="ldapLocalLoginFallback"{" checked" if identity_data.get("ldap_local_login_fallback") == "1" else ""}><span class="slider"></span></label></span>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Connection timeout</span>
                                    <input type="number" name="ldap_connection_timeout" id="ldapConnectionTimeout" min="1" max="120" value="{h(identity_data.get("ldap_connection_timeout", "5"))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Failure behavior</span>
                                    <select name="ldap_failure_behavior" id="ldapFailureBehavior">
                                        {failure_options}
                                    </select>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Role mapping</span>
                                    <input type="hidden" name="{LDAP_ROLE_MAPPING_SETTING}" id="ldapRoleMappingsJson">
                                    <div id="ldapRoleMappingsList" class="ldap-role-mapping-list"></div>
                                    <button type="button" id="ldapAddRoleMappingBtn" class="ldap-role-mapping-add"><i class="fa-solid fa-plus"></i> Add Role Mapping</button>
                                </div>
                            </div>
                        </details>
                    </div>
                    <div id="oidcIdentityFields" style="display:none;">
                        <details class="ldap-settings-dropdown" id="oidcSettingsDropdown">
                            <summary>OIDC Settings</summary>
                            <div class="ldap-settings-panel">
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Discovery URL</span>
                                    <input type="text" name="oidc_discovery_url" id="oidcDiscoveryUrl" value="{h(identity_data.get("oidc_discovery_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Client ID</span>
                                    <input type="text" name="oidc_client_id" id="oidcClientId" value="{h(identity_data.get("oidc_client_id", ""))}" autocomplete="off">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Client secret</span>
                                    <input type="password" name="oidc_client_secret" id="oidcClientSecret" value="" autocomplete="off" placeholder="Leave blank to keep current client secret">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Password change URL</span>
                                    <input type="text" name="oidc_password_change_url" id="oidcPasswordChangeUrl" value="{h(identity_data.get("oidc_password_change_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Scope</span>
                                    <input type="text" name="oidc_scope" id="oidcScope" value="{h(identity_data.get("oidc_scope", OIDC_SETTING_DEFAULTS['oidc_scope']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Username claim</span>
                                    <input type="text" name="oidc_username_claim" id="oidcUsernameClaim" value="{h(identity_data.get("oidc_username_claim", OIDC_SETTING_DEFAULTS['oidc_username_claim']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Display name claim</span>
                                    <input type="text" name="oidc_display_name_claim" id="oidcDisplayNameClaim" value="{h(identity_data.get("oidc_display_name_claim", OIDC_SETTING_DEFAULTS['oidc_display_name_claim']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Email claim</span>
                                    <input type="text" name="oidc_email_claim" id="oidcEmailClaim" value="{h(identity_data.get("oidc_email_claim", OIDC_SETTING_DEFAULTS['oidc_email_claim']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Groups claim</span>
                                    <input type="text" name="oidc_groups_claim" id="oidcGroupsClaim" value="{h(identity_data.get("oidc_groups_claim", OIDC_SETTING_DEFAULTS['oidc_groups_claim']))}">
                                </div>
                                <div class="ldap-settings-dropdown nested-identity-settings" id="oidcScimDropdown">
                                    <div class="ldap-settings-header">SCIM Settings</div>
                                    <div class="ldap-settings-panel scim-settings-fields" id="oidcScimFields">
                                        <div class="info-row">
                                            <span class="info-label">Enable SCIM</span>
                                            <span><label class="switch"><input type="checkbox" name="oidc_scim_enabled" id="oidcScimEnabled"{" checked" if identity_data.get("oidc_scim_enabled") == "1" else ""}><span class="slider"></span></label></span>
                                        </div>
                                        <div id="oidcScimConfigRows">
                                            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                                <span class="info-label">Base URL</span>
                                                <input type="text" name="oidc_scim_base_url" id="oidcScimBaseUrl" value="{h(identity_data.get('oidc_scim_base_url', ''))}">
                                            </div>
                                            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                                <span class="info-label">Bearer token</span>
                                                <input type="password" name="oidc_scim_bearer_token" id="oidcScimBearerToken" value="" autocomplete="off" placeholder="Leave blank to keep current bearer token">
                                            </div>
                                            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                                <span class="info-label">Connection timeout</span>
                                                <input type="number" name="oidc_scim_timeout" id="oidcScimTimeout" min="1" max="120" value="{h(identity_data.get('oidc_scim_timeout', '5'))}">
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Role mapping</span>
                                    <input type="hidden" name="{OIDC_ROLE_MAPPING_SETTING}" id="oidcRoleMappingsJson">
                                    <div id="oidcRoleMappingsList" class="ldap-role-mapping-list"></div>
                                    <button type="button" id="oidcAddRoleMappingBtn" class="ldap-role-mapping-add"><i class="fa-solid fa-plus"></i> Add Role Mapping</button>
                                </div>
                            </div>
                        </details>
                    </div>
                    <div id="samlIdentityFields" style="display:none;">
                        <details class="ldap-settings-dropdown" id="samlSettingsDropdown">
                            <summary>SAML Settings</summary>
                            <div class="ldap-settings-panel">
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">IdP entity ID</span>
                                    <input type="text" name="saml_idp_entity_id" id="samlIdpEntityId" value="{h(identity_data.get("saml_idp_entity_id", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">SSO URL</span>
                                    <input type="text" name="saml_sso_url" id="samlSsoUrl" value="{h(identity_data.get("saml_sso_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">X.509 certificate</span>
                                    <textarea name="saml_x509_certificate" id="samlX509Certificate">{h(identity_data.get("saml_x509_certificate", ""))}</textarea>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Password change URL</span>
                                    <input type="text" name="saml_password_change_url" id="samlPasswordChangeUrl" value="{h(identity_data.get("saml_password_change_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Username attribute</span>
                                    <input type="text" name="saml_username_attribute" id="samlUsernameAttribute" value="{h(identity_data.get("saml_username_attribute", SAML_SETTING_DEFAULTS['saml_username_attribute']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Display name attribute</span>
                                    <input type="text" name="saml_display_name_attribute" id="samlDisplayNameAttribute" value="{h(identity_data.get("saml_display_name_attribute", SAML_SETTING_DEFAULTS['saml_display_name_attribute']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Email attribute</span>
                                    <input type="text" name="saml_email_attribute" id="samlEmailAttribute" value="{h(identity_data.get("saml_email_attribute", SAML_SETTING_DEFAULTS['saml_email_attribute']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Groups attribute</span>
                                    <input type="text" name="saml_groups_attribute" id="samlGroupsAttribute" value="{h(identity_data.get("saml_groups_attribute", SAML_SETTING_DEFAULTS['saml_groups_attribute']))}">
                                </div>
                                <div class="ldap-settings-dropdown nested-identity-settings" id="samlScimDropdown">
                                    <div class="ldap-settings-header">SCIM Settings</div>
                                    <div class="ldap-settings-panel scim-settings-fields" id="samlScimFields">
                                        <div class="info-row">
                                            <span class="info-label">Enable SCIM</span>
                                            <span><label class="switch"><input type="checkbox" name="saml_scim_enabled" id="samlScimEnabled"{" checked" if identity_data.get("saml_scim_enabled") == "1" else ""}><span class="slider"></span></label></span>
                                        </div>
                                        <div id="samlScimConfigRows">
                                            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                                <span class="info-label">Base URL</span>
                                                <input type="text" name="saml_scim_base_url" id="samlScimBaseUrl" value="{h(identity_data.get('saml_scim_base_url', ''))}">
                                            </div>
                                            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                                <span class="info-label">Bearer token</span>
                                                <input type="password" name="saml_scim_bearer_token" id="samlScimBearerToken" value="" autocomplete="off" placeholder="Leave blank to keep current bearer token">
                                            </div>
                                            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                                <span class="info-label">Connection timeout</span>
                                                <input type="number" name="saml_scim_timeout" id="samlScimTimeout" min="1" max="120" value="{h(identity_data.get('saml_scim_timeout', '5'))}">
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Role mapping</span>
                                    <input type="hidden" name="{SAML_ROLE_MAPPING_SETTING}" id="samlRoleMappingsJson">
                                    <div id="samlRoleMappingsList" class="ldap-role-mapping-list"></div>
                                    <button type="button" id="samlAddRoleMappingBtn" class="ldap-role-mapping-add"><i class="fa-solid fa-plus"></i> Add Role Mapping</button>
                                </div>
                            </div>
                        </details>
                    </div>
                </div>
                <h4 style="margin-top:20px;">Login Banner</h4>
                <p>Show an optional message before users sign in.</p>
                <div class="info-row" style="border-bottom: none;">
                    <span class="info-label">Enable Banner</span>
                    <span><label class="switch"><input type="checkbox" name="login_banner_enabled" id="bannerToggle"{" checked" if banner_enabled else ""}><span class="slider"></span></label></span>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none;">
                    <span class="info-label">Title</span>
                    <input type="text" name="login_banner_title" id="bannerTitle" value="{h(data.get("login_banner_title", ""))}"{banner_disabled}>
                </div>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                    <span class="info-label">Message</span>
                    <textarea name="login_banner_message" id="bannerMessage"{banner_disabled}>{h(data.get("login_banner_message", ""))}</textarea>
                </div>
                <h4 style="margin-top:20px;">CAPTCHA</h4>
                <p>Enabling CAPTCHA is highly recommended if you are making the web interface public to protect your server from automated login attempts. However, it's not a replacement for other security measures.</p>
                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none;">
                    <span class="info-label">Provider</span>
                    <select name="login_captcha_provider" id="captchaProvider">
                        {captcha_options}
                    </select>
                    <span id="captchaProviderHint" class="info-description"></span>
                </div>
                <div class="info-row captcha-key-row" id="captchaSiteKeyRow" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none;">
                    <span class="info-label">Site Key</span>
                    <input type="text" name="login_captcha_site_key" id="captchaSiteKey" value="{h(data.get("login_captcha_site_key", ""))}" autocomplete="off">
                </div>
                <div class="info-row captcha-key-row" id="captchaSecretKeyRow" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none;">
                    <span class="info-label">Secret Key</span>
                    <input type="password" name="login_captcha_secret_key" id="captchaSecretKey" value="" autocomplete="off" placeholder="Leave blank to keep current secret key">
                </div>
                <div class="info-row" style="border-bottom: none;">
                    <span class="info-label">
                        Require CAPTCHA only for external IP addresses
                        <span class="info-description">Enabled by default. Private, loopback, and other non-public client IPs skip CAPTCHA.</span>
                    </span>
                    <span><label class="switch"><input type="checkbox" name="login_captcha_external_only" id="captchaExternalOnly"{" checked" if data.get("login_captcha_external_only", "1") == "1" else ""}><span class="slider"></span></label></span>
                </div>
                <div class="settings-section-divider" style="margin-top: 20px; border-bottom: none; padding-bottom: 0; border-top: 1px solid #333; padding-top: 16px;">
                    <div class="info-row" style="border-bottom: none;">
                        <div class="info-label"><h4 style="margin:0;">Notify users whose accounts are about to expire</h4></div>
                        <span><label class="switch"><input type="checkbox" name="{ACCOUNT_EXPIRATION_NOTIFY_SETTING}" id="accountExpirationNotifyToggle"{" checked" if data.get(ACCOUNT_EXPIRATION_NOTIFY_SETTING, "1") == "1" else ""}><span class="slider"></span></label></span>
                    </div>
                </div>
                <div class="settings-section-divider" style="border-bottom: none; padding-bottom: 0; border-top: 1px solid #333; padding-top: 16px;">
                    <div class="info-row" style="border-bottom: none;">
                        <div class="info-label">
                            <h4 style="margin:0;">Enable guest receiver</h4>
                            <span class="info-description">When enabled, a new recipient becomes available which allows logged out users to receive messages. Anything sent to this recipient will be able to be seen by anyone able to access the web interface.</span>
                        </div>
                        <span><label class="switch"><input type="checkbox" name="{GUEST_RECEIVER_SETTING}" id="guestReceiverToggle"{" checked" if data.get(GUEST_RECEIVER_SETTING, "0") == "1" else ""}><span class="slider"></span></label></span>
                    </div>
                </div>
                <input type="hidden" name="save_login_settings" value="1">
                <div style="margin-top:20px; display:flex; align-items:center;">
                    <button type="button" id="saveLoginBtn">Save Settings</button>
                    <span id="save-status" class="save-status"></span>
                </div>
            </form>
        </div>
    </div>"""
    script = r"""
document.addEventListener('DOMContentLoaded', function() {
    const bannerToggle = document.getElementById('bannerToggle');
    const bannerTitle = document.getElementById('bannerTitle');
    const bannerMessage = document.getElementById('bannerMessage');
    const captchaProvider = document.getElementById('captchaProvider');
    const captchaSiteKeyRow = document.getElementById('captchaSiteKeyRow');
    const captchaSecretKeyRow = document.getElementById('captchaSecretKeyRow');
    const captchaProviderHint = document.getElementById('captchaProviderHint');
    const identityProvider = document.getElementById('identityProvider');
    const localIdentityNotice = document.getElementById('localIdentityNotice');
    const ssoIdentityOptions = document.getElementById('ssoIdentityOptions');
    const ldapIdentityFields = document.getElementById('ldapIdentityFields');
    const oidcIdentityFields = document.getElementById('oidcIdentityFields');
    const samlIdentityFields = document.getElementById('samlIdentityFields');
    const ldapSettingsDropdown = document.getElementById('ldapSettingsDropdown');
    const oidcSettingsDropdown = document.getElementById('oidcSettingsDropdown');
    const samlSettingsDropdown = document.getElementById('samlSettingsDropdown');
    const oidcScimDropdown = document.getElementById('oidcScimDropdown');
    const samlScimDropdown = document.getElementById('samlScimDropdown');
    const oidcScimEnabled = document.getElementById('oidcScimEnabled');
    const samlScimEnabled = document.getElementById('samlScimEnabled');
    const oidcScimConfigRows = document.getElementById('oidcScimConfigRows');
    const samlScimConfigRows = document.getElementById('samlScimConfigRows');
    const ldapTemplate = document.getElementById('ldapTemplate');
    const ldapTemplatePicker = document.getElementById('ldapTemplatePicker');
    const ldapSecure = document.getElementById('ldapSecure');
    const ldapServerPort = document.getElementById('ldapServerPort');
    const ldapLoginField = document.getElementById('ldapLoginField');
    const ldapUserSearchFilter = document.getElementById('ldapUserSearchFilter');
    const ldapDisplayNameField = document.getElementById('ldapDisplayNameField');
    const ldapEmailField = document.getElementById('ldapEmailField');
    const ldapRoleMappingsInput = document.getElementById('ldapRoleMappingsJson');
    const ldapRoleMappingsList = document.getElementById('ldapRoleMappingsList');
    const ldapAddRoleMappingBtn = document.getElementById('ldapAddRoleMappingBtn');
    const oidcRoleMappingsInput = document.getElementById('oidcRoleMappingsJson');
    const oidcRoleMappingsList = document.getElementById('oidcRoleMappingsList');
    const oidcAddRoleMappingBtn = document.getElementById('oidcAddRoleMappingBtn');
    const samlRoleMappingsInput = document.getElementById('samlRoleMappingsJson');
    const samlRoleMappingsList = document.getElementById('samlRoleMappingsList');
    const samlAddRoleMappingBtn = document.getElementById('samlAddRoleMappingBtn');
    const saveLoginBtn = document.getElementById('saveLoginBtn');
    const roleMappingPermissionSections = __OPS_ROLE_MAPPING_PERMISSION_SECTIONS__;
    const roleMappingPermissionLabels = __OPS_ROLE_MAPPING_PERMISSION_LABELS__;
    const roleMappingGroupOptions = __OPS_ROLE_MAPPING_GROUP_OPTIONS__;
    const roleMappingMessageOptions = __OPS_ROLE_MAPPING_MESSAGE_OPTIONS__;
    const roleMappingBellScheduleOptions = __OPS_ROLE_MAPPING_BELL_SCHEDULE_OPTIONS__;
    const roleMappingDefaultPermissions = __OPS_ROLE_MAPPING_DEFAULT_PERMISSIONS__;
    const initialLdapRoleMappings = __OPS_LDAP_ROLE_MAPPINGS__;
    const initialOidcRoleMappings = __OPS_OIDC_ROLE_MAPPINGS__;
    const initialSamlRoleMappings = __OPS_SAML_ROLE_MAPPINGS__;

    const ldapTemplates = {
        generic: {
            port: '389',
            securePort: '636',
            loginField: 'uid',
            searchFilter: '({field}={username})',
            displayNameField: 'cn',
            emailField: 'mail'
        },
        'active-directory': {
            port: '389',
            securePort: '636',
            loginField: 'sAMAccountName',
            searchFilter: '(&(objectClass=user)({field}={username}))',
            displayNameField: 'displayName',
            emailField: 'mail'
        }
    };

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function uniqueTokenList(values) {
        const list = Array.isArray(values) ? values : [];
        const seen = new Set();
        const normalized = [];
        list.forEach(function(token) {
            const value = String(token || '').trim();
            if (!value || seen.has(value)) return;
            seen.add(value);
            normalized.push(value);
        });
        return normalized;
    }

    function normalizePermissionSelection(value, role, mapping) {
        if (Array.isArray(value)) return uniqueTokenList(value);
        const tokens = String(value || '')
            .replace(/;/g, ',')
            .split(',')
            .map(function(token) { return String(token || '').trim(); })
            .filter(Boolean);
        if (!tokens.length && role === 'user') {
            return roleMappingDefaultPermissions.slice();
        }
        return uniqueTokenList(tokens);
    }

    function normalizeRoleMapping(mapping) {
        const source = mapping && typeof mapping === 'object' ? mapping : {};
        const roleValue = String(source.role || 'receiver').trim().toLowerCase();
        const role = ['none', 'admin', 'user', 'receiver'].indexOf(roleValue) >= 0 ? roleValue : 'receiver';
        const fallback = source.fallback === '1' || source.fallback === 1 || source.fallback === true || String(source.group_match || source.group || '').trim().toLowerCase() === 'other';
        const normalized = {
            group_match: fallback ? 'other' : String(source.group_match || source.group || '').trim(),
            claim_match: fallback ? '' : String(source.claim_match || source.match_claims || source.claims || '').replace(/\r/g, '').trim(),
            recipient_groups: uniqueTokenList(source.recipient_groups),
            role: role,
            userperm: normalizePermissionSelection(source.userperm, role, source),
            restrict_groups: source.restrict_groups === '1' || source.restrict_groups === 1 || source.restrict_groups === true ? '1' : '0',
            allowed_groups: uniqueTokenList(source.allowed_groups),
            restrict_messages: source.restrict_messages === '1' || source.restrict_messages === 1 || source.restrict_messages === true ? '1' : '0',
            allowed_messages: uniqueTokenList(source.allowed_messages),
            restrict_bell_schedules: source.restrict_bell_schedules === '1' || source.restrict_bell_schedules === 1 || source.restrict_bell_schedules === true ? '1' : '0',
            allowed_bell_schedules: uniqueTokenList(source.allowed_bell_schedules),
            fallback: fallback ? '1' : '0'
        };
        if (normalized.role !== 'user') {
            normalized.userperm = [];
            normalized.restrict_groups = '0';
            normalized.allowed_groups = [];
            normalized.restrict_messages = '0';
            normalized.allowed_messages = [];
            normalized.restrict_bell_schedules = '0';
            normalized.allowed_bell_schedules = [];
        }
        return normalized;
    }

    function ensureFallbackRoleMapping(values) {
        const list = Array.isArray(values) ? values.map(normalizeRoleMapping) : [];
        const withoutFallback = list.filter(function(mapping) { return mapping.fallback !== '1'; });
        const fallback = list.find(function(mapping) { return mapping.fallback === '1'; }) || normalizeRoleMapping({ group_match: 'other', fallback: '1', role: 'receiver' });
        withoutFallback.push(fallback);
        return withoutFallback;
    }

    function roleMappingPermissionChoices(mapping, values) {
        const selected = new Set(mapping.userperm && mapping.userperm.length ? mapping.userperm : roleMappingDefaultPermissions);
        return values.map(function(value) {
            const label = roleMappingPermissionLabels[value] || value;
            const checked = selected.has(value) ? ' checked' : '';
            return '<label class="ldap-role-permission-choice md-checkbox-container">' +
                '<input type="checkbox" data-role-mapping-permission="' + escapeHtml(value) + '"' + checked + '>' +
                '<span class="md-checkmark"></span>' +
                '<span class="md-checkbox-text">' + escapeHtml(label) + '</span>' +
                '</label>';
        }).join('');
    }

    function roleMappingScopeChoices(values, selectedValues, dataAttribute) {
        const selected = new Set(selectedValues || []);
        if (!values.length) {
            return '<div class="ldap-role-empty">None yet.</div>';
        }
        return values.map(function(row) {
            const checked = selected.has(row.id) ? ' checked' : '';
            return '<label class="ldap-role-scope-choice md-checkbox-container">' +
                '<input type="checkbox" ' + dataAttribute + '="' + escapeHtml(row.id) + '"' + checked + '>' +
                '<span class="md-checkmark"></span>' +
                '<span class="md-checkbox-text">' + escapeHtml(row.label || row.id) + '</span>' +
                '</label>';
        }).join('');
    }

    function roleMappingRecipientSummary(values, selectedValues) {
        const selected = Array.isArray(selectedValues) ? selectedValues : [];
        if (!values.length) {
            return 'No groups available';
        }
        if (!selected.length) {
            return 'Add to recipient groups';
        }
        const labelsById = {};
        values.forEach(function(row) {
            const id = String((row || {}).id || '').trim();
            if (!id) return;
            labelsById[id] = String((row || {}).label || id).trim() || id;
        });
        const labels = selected
            .map(function(id) {
                const key = String(id || '').trim();
                if (!key) return '';
                return labelsById[key] || key;
            })
            .filter(Boolean);
        if (!labels.length) {
            return 'Add to recipient groups';
        }
        return labels.join(', ');
    }

    function roleMappingRecipientDropdown(values, selectedValues) {
        const summaryText = roleMappingRecipientSummary(values, selectedValues);
        const recipientChoices = roleMappingScopeChoices(values, selectedValues, 'data-role-mapping-recipient-group-id');
        return '<div class="ldap-role-mapping-recipient-dropdown" data-role-mapping-recipient-dropdown="1">' +
            '<button type="button" class="ldap-role-mapping-recipient-trigger" data-role-mapping-recipient-trigger="1">' +
                '<span class="summary" data-role-mapping-recipient-summary="1">' + escapeHtml(summaryText) + '</span>' +
                '<i class="fa-solid fa-chevron-down caret"></i>' +
            '</button>' +
            '<div class="ldap-role-mapping-recipient-menu" data-role-mapping-recipient-menu="1">' +
                '<div class="ldap-role-scope-list" style="margin-top:0; max-height:180px;">' + recipientChoices + '</div>' +
            '</div>' +
        '</div>';
    }

    function roleMappingUserPanel(mapping) {
        if (mapping.role !== 'user') return '';
        const permissionBlocks = roleMappingPermissionSections.map(function(section) {
            return '<div class="ldap-role-permission-block">' +
                '<div class="ldap-role-permission-title">' + escapeHtml(section.title) + '</div>' +
                '<div class="ldap-role-permission-grid">' + roleMappingPermissionChoices(mapping, section.values || []) + '</div>' +
                '</div>';
        }).join('');
        const groupListStyle = mapping.restrict_groups === '1' ? '' : ' style="display:none;"';
        const messageListStyle = mapping.restrict_messages === '1' ? '' : ' style="display:none;"';
        const bellScheduleListStyle = mapping.restrict_bell_schedules === '1' ? '' : ' style="display:none;"';
        return '<details class="ldap-role-user-permissions">' +
            '<summary><span>User Permissions</span><i class="fa-solid fa-chevron-down"></i></summary>' +
            '<div class="ldap-role-user-permissions-body">' +
                '<div class="ldap-role-permission-stack">' + permissionBlocks + '</div>' +
                '<div class="ldap-role-scope-panel">' +
                    '<div class="ldap-role-scope-box">' +
                        '<div class="ldap-role-scope-toggle">' +
                            '<span>Restrict Groups</span>' +
                            '<span><label class="switch"><input type="checkbox" data-role-mapping-restrict-groups="1"' + (mapping.restrict_groups === '1' ? ' checked' : '') + '><span class="slider"></span></label></span>' +
                        '</div>' +
                        '<div class="ldap-role-scope-list"' + groupListStyle + '>' + roleMappingScopeChoices(roleMappingGroupOptions, mapping.allowed_groups, 'data-role-mapping-group-id') + '</div>' +
                    '</div>' +
                    '<div class="ldap-role-scope-box">' +
                        '<div class="ldap-role-scope-toggle">' +
                            '<span>Restrict Messages</span>' +
                            '<span><label class="switch"><input type="checkbox" data-role-mapping-restrict-messages="1"' + (mapping.restrict_messages === '1' ? ' checked' : '') + '><span class="slider"></span></label></span>' +
                        '</div>' +
                        '<div class="ldap-role-scope-list"' + messageListStyle + '>' + roleMappingScopeChoices(roleMappingMessageOptions, mapping.allowed_messages, 'data-role-mapping-message-id') + '</div>' +
                    '</div>' +
                    '<div class="ldap-role-scope-box">' +
                        '<div class="ldap-role-scope-toggle">' +
                            '<span>Restrict Bell Schedules</span>' +
                            '<span><label class="switch"><input type="checkbox" data-role-mapping-restrict-bell-schedules="1"' + (mapping.restrict_bell_schedules === '1' ? ' checked' : '') + '><span class="slider"></span></label></span>' +
                        '</div>' +
                        '<div class="ldap-role-scope-list"' + bellScheduleListStyle + '>' + roleMappingScopeChoices(roleMappingBellScheduleOptions, mapping.allowed_bell_schedules, 'data-role-mapping-bell-schedule-id') + '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '</details>';
    }

    function collectCheckedValues(container, selector, attributeName) {
        return Array.from(container.querySelectorAll(selector))
            .filter(function(input) { return input.checked; })
            .map(function(input) { return String(input.getAttribute(attributeName) || '').trim(); })
            .filter(Boolean);
    }

    function createEmptyRoleMapping() {
        return {
            group_match: '',
            claim_match: '',
            recipient_groups: [],
            role: 'receiver',
            userperm: [],
            restrict_groups: '0',
            allowed_groups: [],
            restrict_messages: '0',
            allowed_messages: [],
            restrict_bell_schedules: '0',
            allowed_bell_schedules: [],
            fallback: '0'
        };
    }

    function createRoleMappingEditor(listElement, inputElement, addButton, initialState) {
        let mappings = Array.isArray(initialState) ? initialState : [];

        function currentRoleMappings() {
            if (!Array.isArray(mappings)) return ensureFallbackRoleMapping([]);
            return ensureFallbackRoleMapping(mappings).filter(function(mapping) {
                return mapping && typeof mapping === 'object';
            });
        }

        function syncInput() {
            mappings = currentRoleMappings();
            if (!inputElement) return;
            inputElement.value = JSON.stringify(mappings);
        }

        function render() {
            if (!listElement) return;
            mappings = currentRoleMappings();
            listElement.innerHTML = mappings.map(function(mapping, index) {
                const roleOptions = [
                    ['none', 'None'],
                    ['admin', 'Administrator'],
                    ['user', 'User'],
                    ['receiver', 'Receiver']
                ].map(function(option) {
                    return '<option value="' + option[0] + '"' + (mapping.role === option[0] ? ' selected' : '') + '>' + option[1] + '</option>';
                }).join('');
                const recipientDropdown = roleMappingRecipientDropdown(roleMappingGroupOptions, mapping.recipient_groups);
                const matchFields = mapping.fallback === '1'
                    ? '<div class="ldap-role-mapping-field ldap-role-mapping-field-fallback">' +
                        '<div class="ldap-role-mapping-fallback-text">Other/Unmatched</div>' +
                      '</div>'
                    : '<div class="ldap-role-mapping-field">' +
                        '<span class="info-label">Match group</span>' +
                        '<input type="text" data-role-mapping-group-match value="' + escapeHtml(mapping.group_match) + '" placeholder="any">' +
                      '</div>' +
                      '<div class="ldap-role-mapping-field">' +
                        '<span class="info-label">Match claims</span>' +
                        '<textarea data-role-mapping-claim-match rows="1" placeholder="any" style="height:42px;min-height:42px;max-height:42px;resize:none;overflow:auto;">' + escapeHtml(mapping.claim_match) + '</textarea>' +
                      '</div>';
                const removeAction = mapping.fallback === '1'
                    ? '<div></div>'
                    : '<button type="button" class="ldap-role-mapping-remove" data-role-mapping-remove="1" style="background:#C62828;color:#FFF;border:none;padding:10px 14px;border-radius:8px;display:inline-flex;align-items:center;gap:8px;cursor:pointer;"><i class="fa-solid fa-trash"></i><span>Delete</span></button>';
                return '<div class="ldap-role-mapping-card" data-role-mapping-index="' + index + '">' +
                    '<div class="ldap-role-mapping-head">' +
                        matchFields +
                        '<div class="ldap-role-mapping-field">' +
                            '<span class="info-label">Recipient groups</span>' +
                            recipientDropdown +
                        '</div>' +
                        '<div class="ldap-role-mapping-field">' +
                            '<span class="info-label">Role</span>' +
                            '<select data-role-mapping-role>' + roleOptions + '</select>' +
                        '</div>' +
                        '<div class="ldap-role-mapping-field" style="justify-content:flex-end;">' +
                            '<span class="info-label">&nbsp;</span>' +
                            removeAction +
                        '</div>' +
                    '</div>' +
                    roleMappingUserPanel(mapping) +
                '</div>';
            }).join('');
            bindEvents();
            syncInput();
        }

        function bindEvents() {
            if (!listElement) return;
            function closeRecipientDropdowns(exceptElement) {
                Array.from(listElement.querySelectorAll('[data-role-mapping-recipient-dropdown="1"]')).forEach(function(dropdown) {
                    if (exceptElement && dropdown === exceptElement) return;
                    dropdown.classList.remove('open');
                });
            }
            Array.from(listElement.querySelectorAll('[data-role-mapping-index]')).forEach(function(card) {
                const index = parseInt(card.getAttribute('data-role-mapping-index') || '-1', 10);
                if (index < 0 || !mappings[index]) return;
                const groupInput = card.querySelector('[data-role-mapping-group-match]');
                const claimInput = card.querySelector('[data-role-mapping-claim-match]');
                const roleSelect = card.querySelector('[data-role-mapping-role]');
                const removeButton = card.querySelector('[data-role-mapping-remove]');
                const recipientDropdown = card.querySelector('[data-role-mapping-recipient-dropdown="1"]');
                const recipientTrigger = card.querySelector('[data-role-mapping-recipient-trigger="1"]');
                const restrictGroupsToggle = card.querySelector('[data-role-mapping-restrict-groups]');
                const restrictMessagesToggle = card.querySelector('[data-role-mapping-restrict-messages]');
                const restrictBellSchedulesToggle = card.querySelector('[data-role-mapping-restrict-bell-schedules]');

                if (groupInput) {
                    groupInput.addEventListener('input', function() {
                        mappings[index].group_match = String(this.value || '').trim();
                        syncInput();
                    });
                }
                if (claimInput) {
                    claimInput.addEventListener('input', function() {
                        mappings[index].claim_match = String(this.value || '').replace(/\r/g, '').trim();
                        syncInput();
                    });
                }
                if (roleSelect) {
                    roleSelect.addEventListener('change', function() {
                        const role = String(this.value || 'receiver').trim().toLowerCase();
                        mappings[index].role = role;
                        if (role === 'user') {
                            if (!Array.isArray(mappings[index].userperm) || !mappings[index].userperm.length) {
                                mappings[index].userperm = roleMappingDefaultPermissions.slice();
                            }
                        } else {
                            mappings[index].userperm = [];
                            mappings[index].restrict_groups = '0';
                            mappings[index].allowed_groups = [];
                            mappings[index].restrict_messages = '0';
                            mappings[index].allowed_messages = [];
                            mappings[index].restrict_bell_schedules = '0';
                            mappings[index].allowed_bell_schedules = [];
                        }
                        render();
                    });
                }
                if (removeButton) {
                    removeButton.addEventListener('click', function() {
                        mappings.splice(index, 1);
                        render();
                    });
                }
                if (recipientTrigger && recipientDropdown) {
                    recipientTrigger.addEventListener('click', function(event) {
                        event.preventDefault();
                        const willOpen = !recipientDropdown.classList.contains('open');
                        closeRecipientDropdowns(recipientDropdown);
                        if (willOpen) recipientDropdown.classList.add('open');
                    });
                }
                Array.from(card.querySelectorAll('[data-role-mapping-recipient-group-id]')).forEach(function(input) {
                    input.addEventListener('change', function() {
                        mappings[index].recipient_groups = collectCheckedValues(card, '[data-role-mapping-recipient-group-id]', 'data-role-mapping-recipient-group-id');
                        const summary = card.querySelector('[data-role-mapping-recipient-summary]');
                        if (summary) {
                            summary.innerText = roleMappingRecipientSummary(roleMappingGroupOptions, mappings[index].recipient_groups);
                        }
                        syncInput();
                    });
                });
                Array.from(card.querySelectorAll('[data-role-mapping-permission]')).forEach(function(input) {
                    input.addEventListener('change', function() {
                        mappings[index].userperm = collectCheckedValues(card, '[data-role-mapping-permission]', 'data-role-mapping-permission');
                        syncInput();
                    });
                });
                if (restrictGroupsToggle) {
                    restrictGroupsToggle.addEventListener('change', function() {
                        mappings[index].restrict_groups = this.checked ? '1' : '0';
                        if (!this.checked) mappings[index].allowed_groups = [];
                        render();
                    });
                }
                if (restrictMessagesToggle) {
                    restrictMessagesToggle.addEventListener('change', function() {
                        mappings[index].restrict_messages = this.checked ? '1' : '0';
                        if (!this.checked) mappings[index].allowed_messages = [];
                        render();
                    });
                }
                if (restrictBellSchedulesToggle) {
                    restrictBellSchedulesToggle.addEventListener('change', function() {
                        mappings[index].restrict_bell_schedules = this.checked ? '1' : '0';
                        if (!this.checked) mappings[index].allowed_bell_schedules = [];
                        render();
                    });
                }
                Array.from(card.querySelectorAll('[data-role-mapping-group-id]')).forEach(function(input) {
                    input.addEventListener('change', function() {
                        mappings[index].allowed_groups = collectCheckedValues(card, '[data-role-mapping-group-id]', 'data-role-mapping-group-id');
                        syncInput();
                    });
                });
                Array.from(card.querySelectorAll('[data-role-mapping-message-id]')).forEach(function(input) {
                    input.addEventListener('change', function() {
                        mappings[index].allowed_messages = collectCheckedValues(card, '[data-role-mapping-message-id]', 'data-role-mapping-message-id');
                        syncInput();
                    });
                });
                Array.from(card.querySelectorAll('[data-role-mapping-bell-schedule-id]')).forEach(function(input) {
                    input.addEventListener('change', function() {
                        mappings[index].allowed_bell_schedules = collectCheckedValues(card, '[data-role-mapping-bell-schedule-id]', 'data-role-mapping-bell-schedule-id');
                        syncInput();
                    });
                });
            });
            if (!window.__opsRoleMappingRecipientDropdownBound) {
                document.addEventListener('click', function(event) {
                    if (event.target && event.target.closest && event.target.closest('[data-role-mapping-recipient-dropdown="1"]')) return;
                    Array.from(document.querySelectorAll('[data-role-mapping-recipient-dropdown="1"]')).forEach(function(dropdown) {
                        dropdown.classList.remove('open');
                    });
                });
                window.__opsRoleMappingRecipientDropdownBound = true;
            }
        }

        if (addButton) {
            addButton.addEventListener('click', function() {
                mappings = currentRoleMappings();
                mappings.push(createEmptyRoleMapping());
                render();
            });
        }

        return {
            render: render,
            syncInput: syncInput
        };
    }

    if (bannerToggle) {
        bannerToggle.addEventListener('change', function() {
            bannerTitle.disabled = !this.checked;
            bannerMessage.disabled = !this.checked;
        });
    }

    function syncCaptchaFields() {
        if (!captchaProvider) return;
        const value = captchaProvider.value;
        const usesExternalKeys = value === 'turnstile' || value === 'recaptcha';
        if (captchaSiteKeyRow) captchaSiteKeyRow.style.display = usesExternalKeys ? 'flex' : 'none';
        if (captchaSecretKeyRow) captchaSecretKeyRow.style.display = usesExternalKeys ? 'flex' : 'none';
        if (captchaProviderHint) {
            if (value === 'basic') {
                captchaProviderHint.innerText = 'The server generates a local image challenge.';
            } else if (value === 'turnstile') {
                captchaProviderHint.innerText = 'Enter the Cloudflare Turnstile site key and secret key.';
            } else if (value === 'recaptcha') {
                captchaProviderHint.innerText = 'Enter the Google reCAPTCHA site key and secret key.';
            } else {
                captchaProviderHint.innerText = 'No CAPTCHA will be shown on the login page.';
            }
        }
    }

    function syncIdentityProvider(userChanged) {
        const value = identityProvider ? identityProvider.value : 'local';
        const isLdap = value === 'ldap';
        const isOidc = value === 'oidc';
        const isSaml = value === 'saml';
        const isLocal = value === 'local';
        const isSso = isOidc || isSaml;
        if (localIdentityNotice) localIdentityNotice.style.display = isLocal ? 'block' : 'none';
        if (ssoIdentityOptions) ssoIdentityOptions.style.display = isSso ? 'block' : 'none';
        if (ldapIdentityFields) ldapIdentityFields.style.display = isLdap ? 'block' : 'none';
        if (oidcIdentityFields) oidcIdentityFields.style.display = isOidc ? 'block' : 'none';
        if (samlIdentityFields) samlIdentityFields.style.display = isSaml ? 'block' : 'none';
        if (userChanged) {
            if (ldapSettingsDropdown) ldapSettingsDropdown.open = isLdap;
            if (oidcSettingsDropdown) oidcSettingsDropdown.open = isOidc;
            if (samlSettingsDropdown) samlSettingsDropdown.open = isSaml;
        }
    }

    function syncScimSection(toggle, rows) {
        if (!toggle || !rows) return;
        rows.style.display = toggle.checked ? 'block' : 'none';
    }

    function currentTemplate() {
        if (!ldapTemplate) return ldapTemplates.generic;
        return ldapTemplates[ldapTemplate.value] || ldapTemplates.generic;
    }

    function syncLdapPort(force) {
        if (!ldapServerPort) return;
        const template = currentTemplate();
        const current = String(ldapServerPort.value || '').trim();
        const genericPorts = ['389', '636'];
        if (force || current === '' || genericPorts.indexOf(current) >= 0) {
            ldapServerPort.value = ldapSecure && ldapSecure.checked ? template.securePort : template.port;
        }
    }

    function applyLdapTemplate(force) {
        const template = currentTemplate();
        if (ldapLoginField && (force || !ldapLoginField.value.trim())) ldapLoginField.value = template.loginField;
        if (ldapUserSearchFilter && (force || !ldapUserSearchFilter.value.trim())) ldapUserSearchFilter.value = template.searchFilter;
        if (ldapDisplayNameField && (force || !ldapDisplayNameField.value.trim())) ldapDisplayNameField.value = template.displayNameField;
        if (ldapEmailField && (force || !ldapEmailField.value.trim())) ldapEmailField.value = template.emailField;
        syncLdapPort(force);
    }

    if (captchaProvider) {
        captchaProvider.addEventListener('change', syncCaptchaFields);
        syncCaptchaFields();
    }
    if (identityProvider) {
        identityProvider.addEventListener('change', function() { syncIdentityProvider(true); });
        syncIdentityProvider(false);
    }
    if (oidcScimEnabled) {
        oidcScimEnabled.addEventListener('change', function() {
            syncScimSection(oidcScimEnabled, oidcScimConfigRows);
        });
        syncScimSection(oidcScimEnabled, oidcScimConfigRows);
    }
    if (samlScimEnabled) {
        samlScimEnabled.addEventListener('change', function() {
            syncScimSection(samlScimEnabled, samlScimConfigRows);
        });
        syncScimSection(samlScimEnabled, samlScimConfigRows);
    }
    if (ldapTemplatePicker) {
        ldapTemplatePicker.addEventListener('change', function() {
            if (!ldapTemplate || !this.value) return;
            ldapTemplate.value = this.value;
            applyLdapTemplate(true);
            this.value = '';
        });
    }
    if (ldapSecure) {
        ldapSecure.addEventListener('change', function() { syncLdapPort(false); });
    }
    const ldapRoleMappingEditor = createRoleMappingEditor(ldapRoleMappingsList, ldapRoleMappingsInput, ldapAddRoleMappingBtn, initialLdapRoleMappings);
    const oidcRoleMappingEditor = createRoleMappingEditor(oidcRoleMappingsList, oidcRoleMappingsInput, oidcAddRoleMappingBtn, initialOidcRoleMappings);
    const samlRoleMappingEditor = createRoleMappingEditor(samlRoleMappingsList, samlRoleMappingsInput, samlAddRoleMappingBtn, initialSamlRoleMappings);
    if (saveLoginBtn) {
        saveLoginBtn.addEventListener('click', function() {
            ldapRoleMappingEditor.syncInput();
            oidcRoleMappingEditor.syncInput();
            samlRoleMappingEditor.syncInput();
        });
    }
    ldapRoleMappingEditor.render();
    oidcRoleMappingEditor.render();
    samlRoleMappingEditor.render();
    applyLdapTemplate(false);
    postSettings('loginSettingsForm','saveLoginBtn','save-status','Settings saved successfully.', false);
});
"""
    script = script.replace("__OPS_ROLE_MAPPING_PERMISSION_SECTIONS__", json.dumps(role_mapping_permission_sections))
    script = script.replace("__OPS_ROLE_MAPPING_PERMISSION_LABELS__", json.dumps(dict(USER_PERMISSION_LABELS)))
    script = script.replace("__OPS_ROLE_MAPPING_GROUP_OPTIONS__", json.dumps(role_mapping_group_options))
    script = script.replace("__OPS_ROLE_MAPPING_MESSAGE_OPTIONS__", json.dumps(role_mapping_message_options))
    script = script.replace("__OPS_ROLE_MAPPING_BELL_SCHEDULE_OPTIONS__", json.dumps(role_mapping_bell_schedule_options))
    script = script.replace("__OPS_ROLE_MAPPING_DEFAULT_PERMISSIONS__", json.dumps(default_user_permission_values))
    script = script.replace("__OPS_LDAP_ROLE_MAPPINGS__", json.dumps(role_mapping_state))
    script = script.replace("__OPS_OIDC_ROLE_MAPPINGS__", json.dumps(oidc_role_mapping_state))
    script = script.replace("__OPS_SAML_ROLE_MAPPINGS__", json.dumps(saml_role_mapping_state))
    return settings_page("Login Settings", ctx, "login", body, script)
