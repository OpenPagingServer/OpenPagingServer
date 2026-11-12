from srv.web.app import *
from srv.web.chrome_verified_access import SERVICE_ACCOUNT_FILE, store_service_account
from srv.web.kerberos_desktop_auth import KEYTAB_FILE, store_keytab, validate_keytab, write_krb5_config
from srv.web.pages.admin.settings.common import settings_page


IDENTITY_WELCOME = '''
<style>
    .identity-welcome { text-align:center; padding:clamp(32px, 7vw, 88px) 20px; }
    .identity-welcome-icon { position:relative; display:inline-block; font-size:64px; line-height:1; color:var(--ops-accent); }
    .identity-welcome-account { display:block; mask-image:radial-gradient(circle at 94% 90%, transparent .40em, #000 .42em); }
    .identity-welcome-wand { position:absolute; right:-12px; bottom:-9px; font-size:32px; }
    .identity-welcome h1 { font-size:clamp(26px, 4vw, 42px); margin:28px 0 20px; }
    .identity-welcome-actions { display:flex; justify-content:center; gap:12px; flex-wrap:wrap; }
    .identity-welcome-actions a { padding:10px 24px; border-radius:24px; text-decoration:none; font-weight:500; }
    .identity-welcome-primary { background:var(--ops-accent); color:#fff; box-shadow:0 2px 5px #0004; }
    .identity-welcome-primary:hover { background:var(--ops-accent-hover); }
    .identity-welcome-secondary { border:1px solid var(--ops-accent); color:var(--ops-accent); }
</style>
<div class="info-card identity-welcome">
    <div class="identity-welcome-icon" aria-hidden="true">
        <i class="fa-solid fa-circle-user identity-welcome-account"></i>
        <span class="identity-welcome-wand"><i class="fa-solid fa-wand-magic-sparkles"></i></span>
    </div>
    <h1>Configure Single sign-on in a few clicks</h1>
    <div class="identity-welcome-actions">
        <a class="identity-welcome-primary" __WIZARD_LINK_ATTRIBUTES__>Get Started</a>
        <a class="identity-welcome-secondary" href="?setup=manual">Configure manually</a>
    </div>
</div>
'''

IDENTITY_WIZARD = """
<p id="identityWizardProgress" aria-live="polite"></p>
<section id="wizardLdapChoice">
    <h2 tabindex="-1">Do you want to import users from an LDAP directory?</h2>
    <p>Choose Yes if your users are in Active Directory, OpenLDAP, or another LDAP directory.
       Choose No if you manage users in Open Paging Server.</p>
    <fieldset class="identity-wizard-choices">
        <legend>User directory</legend>
        <label><input type="radio" name="wizard_ldap_choice" value="yes"> Yes, import users from LDAP</label>
        <label><input type="radio" name="wizard_ldap_choice" value="no"> No, manage users here</label>
    </fieldset>
</section>
<section id="wizardLdapTemplate" hidden>
    <h2 tabindex="-1">What kind of directory do you use?</h2>
    <p>The template fills in the username, name, email, and search fields for your directory.</p>
</section>
<section id="wizardLdapConnection" hidden>
    <h2 tabindex="-1">How do we connect to your directory?</h2>
    <p>Enter the server and the base DN where user searches should start. If anonymous searches are
       disabled, enter the credentials for an account that can read users and their groups.</p>
</section>
<section id="wizardLdapGroups" hidden>
    <h2 tabindex="-1">What access should imported users have?</h2>
    <p>Choose a role for each directory group you want to map. Use Other/Unmatched to set the default
       for everyone else. The directory must expose group membership through <code>memberOf</code>.</p>
</section>
<section id="wizardSsoChoice" hidden>
    <h2 tabindex="-1">How should users sign in?</h2>
    <p>You can choose for users to login directly on this server, or use an external single-sign on service or server. SSO must return the email or username of a local or LDAP synced user.</p>
    <fieldset class="identity-wizard-choices" aria-label="Sign-in method">
        <label id="wizardLocalSignIn"><input type="radio" name="wizard_sign_in" value="local"> Locally</label>
        <label><input type="radio" name="wizard_sign_in" value="sso"> Single sign-on (SAML/OIDC)</label>
    </fieldset>
</section>
<section id="wizardSsoProtocol" hidden>
    <h2 tabindex="-1">Which single sign-on protocol is being used?</h2>
    <p>Refer to your SSO vendor for help</p>
    <fieldset class="identity-wizard-choices" aria-label="Single sign-on protocol">
        <label><input type="radio" name="wizard_sso_protocol" value="saml"> SAML</label>
        <label><input type="radio" name="wizard_sso_protocol" value="oidc"> OpenID Connect (OIDC)</label>
    </fieldset>
</section>
<section id="wizardSsoConnection" hidden>
    <h2 tabindex="-1">How do we connect to your sign-in provider?</h2>
    <p>Use the settings from your provider. Its username or email must match an existing account.</p>
</section>
<section id="wizardSsoBehavior" hidden>
    <h2 tabindex="-1">How should login be handled?</h2>
    <fieldset class="identity-wizard-choices" aria-label="Login behavior">
        <label><input type="radio" name="wizard_login_behavior" value="require" checked> Require SSO when available (Recommended)</label>
        <label><input type="radio" name="wizard_login_behavior" value="redirect"> Auto-redirect to SSO but always allow local login</label>
        <label><input type="radio" name="wizard_login_behavior" value="select"> Allow the user to select SSO or local login</label>
    </fieldset>
</section>
<section id="wizardReview" hidden>
    <h2 tabindex="-1">Review your identity setup</h2>
    <p id="identityReviewSummary"></p>
    <p id="identityReviewLogin" hidden></p>
    <p id="identityReviewLdap" hidden>Saving runs the first LDAP sync. Users then sync every <span id="identityReviewLdapInterval"></span> seconds.</p>
</section>
<p id="identityWizardError" role="alert" hidden></p>
<p id="identityWizardConnectionStatus" role="status" hidden></p>
<dialog id="identityLdapConnectionFailure" aria-labelledby="identityLdapFailureTitle" aria-describedby="identityLdapFailureDetail">
    <h2 id="identityLdapFailureTitle">Couldn’t connect to LDAP</h2>
    <p id="identityLdapFailureDetail"></p>
    <p>Would you like to change the connection settings or continue setup anyway?</p>
    <div class="identity-actions identity-dialog-actions">
        <button type="button" id="identityLdapReconfigure" class="identity-dialog-filled" autofocus>Reconfigure</button>
        <button type="button" id="identityLdapContinue" class="identity-dialog-outlined">Continue anyway</button>
    </div>
</dialog>
"""


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


def _normalize_ldap_connection_settings(form, existing, enabled=True):
    secure = form.get("ldap_secure") is not None
    bind_password = str(form.get("ldap_bind_password") or "")
    effective_bind_password = bind_password or str(existing.get("ldap_bind_password") or "")
    errors = []
    cleaned = {
        "ldap_server_address": str(form.get("ldap_server_address") or "").strip(),
        "ldap_server_port": str(form.get("ldap_server_port") or "").strip(),
        "ldap_secure": "1" if secure else "0",
        "ldap_ca_certificate": str(form.get("ldap_ca_certificate") or ""),
        "ldap_base_dn": str(form.get("ldap_base_dn") or "").strip(),
        "ldap_bind_username": str(form.get("ldap_bind_username") or "").strip(),
        "ldap_bind_password": effective_bind_password,
        "ldap_login_field": str(form.get("ldap_login_field") or "").strip(),
        "ldap_user_search_filter": str(form.get("ldap_user_search_filter") or "").strip(),
        "ldap_display_name_field": str(form.get("ldap_display_name_field") or "").strip(),
        "ldap_email_field": str(form.get("ldap_email_field") or "").strip(),
        "ldap_connection_timeout": str(form.get("ldap_connection_timeout") or "").strip(),
    }
    for key in ("ldap_disabled_attribute", "ldap_disabled_rule", "ldap_password_expired_attribute", "ldap_password_expired_rule"):
        cleaned[key] = str(form.get(key, existing.get(key, ""))).strip()
    cleaned["ldap_directory_access"] = str(form.get("ldap_directory_access", existing["ldap_directory_access"]))
    cleaned["ldap_password_change_method"] = str(form.get("ldap_password_change_method", existing["ldap_password_change_method"]))
    if cleaned["ldap_directory_access"] not in {"read_write", "read_only"}:
        errors.append("Select a valid directory access mode.")
    if cleaned["ldap_password_change_method"] not in {"standard", "active_directory"}:
        errors.append("Select a valid directory password change method.")
    for prefix in ("ldap_disabled", "ldap_password_expired"):
        attribute = cleaned[f"{prefix}_attribute"]
        rule = cleaned[f"{prefix}_rule"]
        if attribute and not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", attribute):
            errors.append("Enter a valid LDAP account status attribute.")
        if rule and rule != "none" and not attribute:
            errors.append("Enter an LDAP attribute for each account status rule.")
        if rule.startswith(("age:", "mask:")) and not re.fullmatch(r"(?:age|mask):[1-9][0-9]{0,9}", rule):
            errors.append("LDAP age and bit mask rules require a positive integer.")
    timeout_value = _clean_timeout(cleaned["ldap_connection_timeout"])
    if timeout_value is None:
        errors.append("Connection timeout must be between 1 and 120 seconds.")
    else:
        cleaned["ldap_connection_timeout"] = timeout_value
    port_value = _clean_port(cleaned["ldap_server_port"], secure)
    if port_value is None:
        errors.append("Server port must be between 1 and 65535.")
    else:
        cleaned["ldap_server_port"] = port_value

    if enabled:
        if Connection is None or Server is None:
            errors.append("LDAP Python library not installed in running environment")
            return cleaned, errors
        if not _valid_server_address(cleaned["ldap_server_address"]):
            errors.append("Enter a valid LDAP server hostname or IP address.")
        if not cleaned["ldap_base_dn"]:
            errors.append("Base DN is required.")
        if cleaned["ldap_bind_username"] and not cleaned["ldap_bind_password"]:
            errors.append("Bind password is required when bind username is set.")
        for field in ("ldap_login_field", "ldap_display_name_field", "ldap_email_field"):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", cleaned[field]):
                errors.append("LDAP attribute names must contain letters, digits, or hyphens and start with a letter.")
                break
        if not cleaned["ldap_user_search_filter"]:
            errors.append("User search filter is required.")
        ca_value = str(cleaned["ldap_ca_certificate"] or "").strip()
        if secure and ca_value and "-----BEGIN CERTIFICATE-----" not in ca_value and not Path(ca_value).is_file():
            errors.append("CA certificate must be blank, a file path, or PEM text.")
    return cleaned, errors


def _normalize_identity_settings(form, existing):
    existing_identity = identity_provider_settings(existing)
    group_rows = _group_option_rows()
    message_rows = _message_option_rows()
    bell_schedule_rows = _bell_schedule_option_rows()
    valid_group_ids = {str(row.get("id") or "").strip() for row in group_rows if str(row.get("id") or "").strip()}
    valid_message_ids = {str(row.get("messageid") or "").strip() for row in message_rows if str(row.get("messageid") or "").strip()}
    valid_schedule_ids = {str(row.get("id") or "").strip() for row in bell_schedule_rows if str(row.get("id") or "").strip()}
    provider = str(form.get("identity_provider") or "local").strip().lower()
    if provider not in {"local", *REDIRECT_IDENTITY_PROVIDER_VALUES}:
        provider = "local"
    oidc_client_secret = str(form.get("oidc_client_secret") or "").strip()
    effective_oidc_client_secret = oidc_client_secret or str(existing_identity.get("oidc_client_secret") or "").strip()
    errors = []
    try:
        role_mappings = json.loads(form.get(LDAP_ROLE_MAPPING_SETTING) or "[]")
        if not isinstance(role_mappings, list):
            raise ValueError
    except ValueError:
        role_mappings = []
        errors.append("LDAP role mapping data is invalid.")
    normalized_mappings = normalize_ldap_role_mappings(role_mappings, valid_group_ids, valid_message_ids, valid_schedule_ids)
    if any(
        mapping["fallback"] != "1" and not (mapping["group_match"] or mapping["claim_match"])
        for mapping in normalized_mappings
    ):
        errors.append("Enter a directory group for each LDAP role mapping.")
    cleaned = {
        "identity_provider": provider,
        "identity_redirect_auto": "1" if form.get("identity_redirect_auto") else "0",
        "identity_allow_local_login": "1" if form.get("identity_allow_local_login") else "0",
        "ldap_enabled": "1" if form.get("ldap_enabled") else "0",
        "ldap_template": str(form.get("ldap_template") or "generic").strip().lower() or "generic",
        "ldap_password_change_url": str(form.get("ldap_password_change_url") or "").strip(),
        "ldap_required_group": "",
        "ldap_admin_group": "",
        "ldap_auto_create_users": "0",
        "ldap_local_login_fallback": "1" if form.get("ldap_local_login_fallback") else "0",
        "ldap_sync_interval": str(form.get("ldap_sync_interval") or "60").strip(),
        "ldap_failure_behavior": str(form.get("ldap_failure_behavior") or "deny").strip().lower(),
        "ldap_group_sync": "1",
        "ldap_default_role": "receiver",
        "oidc_discovery_url": str(form.get("oidc_discovery_url") or "").strip(),
        "oidc_client_id": str(form.get("oidc_client_id") or "").strip(),
        "oidc_client_secret": effective_oidc_client_secret,
        "oidc_password_change_url": str(form.get("oidc_password_change_url") or "").strip(),
        "oidc_scope": str(form.get("oidc_scope") or OIDC_SETTING_DEFAULTS["oidc_scope"]).strip() or OIDC_SETTING_DEFAULTS["oidc_scope"],
        "oidc_username_claim": str(form.get("oidc_username_claim") or OIDC_SETTING_DEFAULTS["oidc_username_claim"]).strip() or OIDC_SETTING_DEFAULTS["oidc_username_claim"],
        "saml_idp_entity_id": str(form.get("saml_idp_entity_id") or "").strip(),
        "saml_sso_url": str(form.get("saml_sso_url") or "").strip(),
        "saml_x509_certificate": str(form.get("saml_x509_certificate") or "").strip(),
        "saml_password_change_url": str(form.get("saml_password_change_url") or "").strip(),
        "saml_username_attribute": str(form.get("saml_username_attribute") or SAML_SETTING_DEFAULTS["saml_username_attribute"]).strip() or SAML_SETTING_DEFAULTS["saml_username_attribute"],
        "oidc_email_claim": str(form.get("oidc_email_claim", existing_identity["oidc_email_claim"])).strip(),
        "saml_email_attribute": str(form.get("saml_email_attribute", existing_identity["saml_email_attribute"])).strip(),
        LDAP_ROLE_MAPPING_SETTING: normalized_mappings,
    }

    if cleaned["ldap_failure_behavior"] not in IDENTITY_FAILURE_BEHAVIORS:
        errors.append("Select a valid failure behavior.")
        cleaned["ldap_failure_behavior"] = "deny"
    for field_name, label in (
        ("oidc_discovery_url", "OIDC discovery URL"),
        ("saml_sso_url", "SAML SSO URL"),
        ("ldap_password_change_url", "LDAP password change URL"),
        ("oidc_password_change_url", "OIDC password change URL"),
        ("saml_password_change_url", "SAML password change URL"),
    ):
        if not _valid_optional_url(cleaned.get(field_name)):
            errors.append(f"{label} must be blank or a valid HTTP/HTTPS URL.")
    interval = ldap_sync_interval(cleaned["ldap_sync_interval"])
    if interval is None:
        errors.append("LDAP sync interval must be between 1 and 86400 seconds.")
    else:
        cleaned["ldap_sync_interval"] = str(interval)
    ldap_settings, ldap_errors = _normalize_ldap_connection_settings(form, existing_identity, cleaned["ldap_enabled"] == "1")
    cleaned.update(ldap_settings)
    errors.extend(ldap_errors)
    if provider == "oidc":
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
        if "openid" not in cleaned["oidc_scope"].split():
            errors.append("OIDC scope must include openid.")
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

    return cleaned, errors


def _write_identity_settings(cleaned):
    execute_many([
        (
            """INSERT INTO systemsettings (parameter, value, description) VALUES (%s, %s, '')
               ON DUPLICATE KEY UPDATE value = VALUES(value)""",
            (key, value),
        )
        for key, value in cleaned.items()
    ])


def _save_identity_settings(cleaned, reset=False, delete_ldap=False, initial_sync=False):
    with LDAP_DIRECTORY_SYNC_LOCK, LDAP_USER_SYNC_LOCK:
        current = identity_provider_settings()
        if identity_disable_prohibited() and (
            reset
            or cleaned["identity_provider"] == "local"
            or (current["ldap_enabled"] == "1" and cleaned["ldap_enabled"] != "1")
        ):
            return jsonify(status="error", message="Disabling identity settings is prohibited."), 403

        delete_ldap = delete_ldap and current["ldap_enabled"] == "1" and cleaned["ldap_enabled"] == "0"
        if reset or delete_ldap:
            step = "Preparing the identity tables"
            try:
                ensure_identity_access_schema()
                step = "Finding synced users"
                with identity_transaction():
                    providers = ("ldap", "oidc", "saml") if reset else ("ldap",)
                    placeholders = ", ".join(["%s"] * len(providers))
                    users = query_all(f"SELECT id FROM users WHERE auth_provider IN ({placeholders}) FOR UPDATE", providers)
                    step = "Removing synced users"
                    for row in users:
                        delete_provider_managed_user(row["id"])
                    if reset:
                        step = "Clearing saved identity settings"
                        execute(
                            "DELETE FROM systemsettings WHERE LEFT(parameter, 5) IN ('ldap_', 'oidc_', 'saml_') "
                            "OR LEFT(parameter, 9) = 'identity_'"
                        )
                    step = "Saving identity settings"
                    _write_identity_settings(cleaned)
                    step = "Committing the changes"
            except Exception as exc:
                message = "Reset failed" if reset else "Could not delete LDAP users"
                error_type = type(exc).__name__
                if isinstance(exc, pymysql.MySQLError) and exc.args and isinstance(exc.args[0], int):
                    error_type = f"database error {exc.args[0]}"
                    if exc.args[0] == 1406 and len(exc.args) > 1:
                        column = re.search(r"Data too long for column '([A-Za-z0-9_.-]+)'", str(exc.args[1]), re.IGNORECASE)
                        if column:
                            error_type += f", value too long for column {column[1]}"
                app.logger.exception("%s while %s", message, step.lower())
                return jsonify(status="error", message=message, detail=f"{step} failed ({error_type})."), 500
        else:
            _write_identity_settings(cleaned)
    response = {"status": "success"}
    if initial_sync and cleaned["ldap_enabled"] == "1":
        try:
            count = sync_ldap_directory(source="setup")
            response["message"] = f"Settings saved. Synced {count} LDAP user(s)."
        except LDAPSyncInProgress:
            response["message"] = "Settings saved. An LDAP sync is already running."
        except LDAPSyncPartialFailure as exc:
            response = {"status": "error", "message": f"Settings saved. {exc} Automatic sync will retry."}
        except Exception:
            app.logger.exception("Initial LDAP directory sync failed")
            response = {
                "status": "error",
                "message": "Settings saved, but the first LDAP sync failed. Automatic sync will retry.",
            }
    if cleaned["ldap_enabled"] == "1":
        start_ldap_sync_worker()
    LDAP_SYNC_WAKE.set()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(response)
    return redirect("/admin/settings/identity")


def desktop_auto_login_section(data):
    product = h(data.get("product_name") or "Open Paging Server")
    csrf = session.setdefault("chromeos_settings_csrf", secrets.token_urlsafe(32))
    enabled = data.get("chromeos_auto_login") == "1"
    kerberos_enabled = data.get("kerberos_auto_login") == "1"
    key_uploaded = SERVICE_ACCOUNT_FILE.is_file()
    keytab_uploaded = KEYTAB_FILE.is_file()
    return f"""
    <section style="margin-top:24px;" aria-labelledby="chromeosSettingsTitle">
        <h4 id="chromeosSettingsTitle">Desktop client auto-login</h4>
        <p>Automatically sign users into their {product} account when they login to their domain account on a managed computer. Sessions created with this method will only be able to receive broadcasts until they login to {product} manually.</p>
        <input type="hidden" name="chromeos_settings_csrf" value="{h(csrf)}">
        <div class="info-row">
            <label class="info-label" id="chromeosEnableLabel" for="chromeosAutoLogin"><strong>Chrome Verified Access (ChromeOS)</strong></label>
            <label class="switch"><input type="checkbox" role="switch" id="chromeosAutoLogin"
                name="chromeos_auto_login" value="1" aria-labelledby="chromeosEnableLabel"
                aria-controls="chromeosSettingsFields" {"checked" if enabled else ""}><span class="slider"></span></label>
        </div>
        <div class="ldap-settings-panel" id="chromeosSettingsFields" {"" if enabled else "hidden"}>
            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                <label class="info-label" for="chromeosWorkspaceDomain">Google Workspace domain</label>
                <input type="text" id="chromeosWorkspaceDomain" name="chromeos_workspace_domain" value="{h(data.get('chromeos_workspace_domain', ''))}">
            </div>
            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                <span class="info-label" id="chromeosServiceAccountLabel">Service account JSON file</span>
                <div aria-labelledby="chromeosServiceAccountLabel">
                    <input type="file" id="chromeosServiceAccountFile" accept=".json,application/json" hidden>
                    <button type="button" id="chromeosUploadKey" {"hidden" if key_uploaded else ""}>Upload</button>
                    <button type="button" id="chromeosDeleteKey" class="identity-delete-button" {"" if key_uploaded else "hidden"}>Delete</button>
                    <span id="chromeosKeyStatus" class="save-status" role="status"></span>
                </div>
            </div>
            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                <label class="info-label" for="chromeosSessionDays">Session lifetime in days</label>
                <input type="number" id="chromeosSessionDays" name="chromeos_session_days" min="1" max="365" value="{h(data.get('chromeos_session_days', '7'))}">
            </div>
        </div>
        <div class="info-row" style="margin-top:20px;">
            <label class="info-label" id="kerberosEnableLabel" for="kerberosAutoLogin"><strong>Kerberos (Active Directory)</strong></label>
            <label class="switch"><input type="checkbox" role="switch" id="kerberosAutoLogin"
                name="kerberos_auto_login" value="1" aria-labelledby="kerberosEnableLabel"
                aria-controls="kerberosSettingsFields" {"checked" if kerberos_enabled else ""}><span class="slider"></span></label>
        </div>
        <div class="ldap-settings-panel" id="kerberosSettingsFields" {"" if kerberos_enabled else "hidden"}>
            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                <label class="info-label" for="kerberosRealm">Kerberos Realm</label>
                <input type="text" id="kerberosRealm" name="kerberos_realm" autocomplete="off" value="{h(data.get('kerberos_realm', ''))}" placeholder="EXAMPLE.COM">
            </div>
            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                <label class="info-label" for="kerberosKdc">Key Distribution Center (KDC)</label>
                <input type="text" id="kerberosKdc" name="kerberos_kdc" autocomplete="off" value="{h(data.get('kerberos_kdc', ''))}" placeholder="dc01.example.com">
            </div>
            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                <label class="info-label" for="kerberosSpn">Service Principal Name (SPN)</label>
                <input type="text" id="kerberosSpn" name="kerberos_spn" autocomplete="off" value="{h(data.get('kerberos_spn', ''))}" placeholder="HTTP/paging.example.com">
            </div>
            <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                <span class="info-label" id="kerberosKeytabLabel">Service Keytab</span>
                <div aria-labelledby="kerberosKeytabLabel">
                    <input type="file" id="kerberosKeytabFile" accept=".keytab,.kt,application/octet-stream" hidden>
                    <button type="button" id="kerberosUploadKeytab" {"hidden" if keytab_uploaded else ""}>Upload</button>
                    <button type="button" id="kerberosDeleteKeytab" class="identity-delete-button" {"" if keytab_uploaded else "hidden"}>Delete</button>
                    <span id="kerberosKeytabStatus" class="save-status" role="status"></span>
                </div>
            </div>
        </div>
    </section>
    """


def chromeos_settings_csrf_valid():
    csrf = session.get("chromeos_settings_csrf")
    supplied = request.form.get("chromeos_settings_csrf", "")
    return bool(csrf and supplied.isascii() and hmac.compare_digest(csrf, supplied))


def handle_request():
    user = require_admin()
    if not isinstance(user, dict):
        return user
    data = settings()
    identity_data = identity_provider_settings(data)
    if request.method == "GET" and request.args.get("ldap_sync_status") == "1":
        return jsonify(sync=ldap_sync_status() if identity_data["ldap_enabled"] == "1" else None)
    prohibited = identity_disable_prohibited()
    ldap_locked = prohibited and identity_data["ldap_enabled"] == "1"
    if request.method == "POST":
        if demo_mode_enabled():
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message="Demo Mode is enabled."), 403
            return demo_mode_page("Identity Settings", legacy_user_context(user), "settings", "settings")
        key_action = request.form.get("chromeos_key_action")
        if key_action in {"upload", "delete"}:
            if not chromeos_settings_csrf_valid():
                return jsonify(status="error", message="Reload Identity Settings and try again."), 403
            try:
                if key_action == "upload":
                    upload = request.files.get("chromeos_service_account_json")
                    if not upload:
                        return jsonify(status="error", message="Choose a service account JSON file."), 400
                    store_service_account(upload)
                else:
                    SERVICE_ACCOUNT_FILE.unlink(missing_ok=True)
            except ImportError:
                app.logger.exception("Chrome Verified Access dependencies are missing")
                return jsonify(status="error", message="Install the updated OPS server dependencies to upload a service account key."), 503
            except ValueError:
                return jsonify(status="error", message="Upload a valid Google service account JSON key under 64 KB."), 400
            except OSError:
                app.logger.warning("Could not update the Chrome Verified Access service account file")
                return jsonify(status="error", message="OPS could not update the service account file."), 500
            return jsonify(status="success", uploaded=SERVICE_ACCOUNT_FILE.is_file())
        kerberos_key_action = request.form.get("kerberos_key_action")
        if kerberos_key_action in {"upload", "delete"}:
            if not chromeos_settings_csrf_valid():
                return jsonify(status="error", message="Reload Identity Settings and try again."), 403
            try:
                if kerberos_key_action == "upload":
                    upload = request.files.get("kerberos_service_keytab")
                    spn = str(request.form.get("kerberos_spn") or "").strip()
                    realm = str(request.form.get("kerberos_realm") or "").strip().upper()
                    if not upload or not spn or not realm:
                        return jsonify(status="error", message="Enter the realm and SPN, then choose a service keytab."), 400
                    store_keytab(upload, f"{spn}@{realm}")
                else:
                    KEYTAB_FILE.unlink(missing_ok=True)
            except ImportError:
                app.logger.exception("Kerberos dependencies are missing")
                return jsonify(status="error", message="Install the updated OPS server dependencies to upload a keytab."), 503
            except ValueError:
                return jsonify(status="error", message="Upload a valid keytab containing the configured SPN (maximum 1 MB)."), 400
            except OSError:
                app.logger.warning("Could not update the Kerberos service keytab")
                return jsonify(status="error", message="OPS could not update the service keytab."), 500
            return jsonify(status="success", uploaded=KEYTAB_FILE.is_file())
        if request.form.get("test_ldap_connection") == "1":
            config, errors = _normalize_ldap_connection_settings(request.form, identity_data)
            if errors:
                return jsonify(status="error", message=" ".join(errors)), 400
            try:
                test_ldap_directory(config)
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                if config["ldap_bind_password"]:
                    message = message.replace(config["ldap_bind_password"], "[redacted]")
                return jsonify(status="error", message=message), 502
            return jsonify(status="success")
        if request.form.get("sync_ldap_users") == "1":
            if identity_data["ldap_enabled"] != "1":
                return jsonify(status="error", message="Enable LDAP user syncing and save your settings first."), 400
            try:
                count = sync_ldap_directory()
            except LDAPSyncInProgress as exc:
                return jsonify(status="error", message=str(exc)), 409
            except LDAPSyncPartialFailure as exc:
                return jsonify(status="error", message=str(exc)), 502
            except Exception:
                app.logger.exception("LDAP directory sync failed")
                return jsonify(status="error", message="LDAP sync failed. Check the directory settings and server log."), 502
            return jsonify(status="success", message=f"Synced {count} LDAP user(s).")
        reset = request.form.get("reset_identity") == "1"
        delete_ldap = request.form.get("delete_ldap_users") == "1"
        requested_provider = str(request.form.get("identity_provider") or "").strip().lower()
        if prohibited and (
            reset
            or requested_provider not in REDIRECT_IDENTITY_PROVIDER_VALUES
            or (ldap_locked and not request.form.get("ldap_enabled"))
        ):
            return jsonify(status="error", message="Disabling identity settings is prohibited."), 403
        if reset or delete_ldap:
            if request.headers.get("X-Requested-With") != "XMLHttpRequest":
                abort(403)
        if reset:
            return _save_identity_settings(
                {**LDAP_SETTING_DEFAULTS, **SSO_SETTING_DEFAULTS, **OIDC_SETTING_DEFAULTS, **SAML_SETTING_DEFAULTS, **DESKTOP_AUTO_LOGIN_DEFAULTS},
                reset=True,
            )
        cleaned, errors = _normalize_identity_settings(request.form, data)
        if "chromeos_settings_csrf" in request.form and request.args.get("setup") != "wizard":
            if not chromeos_settings_csrf_valid():
                return jsonify(status="error", message="Reload Identity Settings and try again."), 403
            enabled = request.form.get("chromeos_auto_login") == "1"
            changes = {"chromeos_auto_login": "1" if enabled else "0"}
            if enabled:
                domain = str(request.form.get("chromeos_workspace_domain") or "").strip().lower()
                days = str(request.form.get("chromeos_session_days") or "").strip()
                if (
                    not re.fullmatch(r"[a-z0-9.-]+\.[a-z0-9-]+", domain)
                    or not days.isascii() or not days.isdecimal() or len(days) > 3 or not 1 <= int(days) <= 365
                ):
                    errors.append("Enter a Workspace domain and a lifetime of 1 to 365 days.")
                if not SERVICE_ACCOUNT_FILE.is_file():
                    errors.append("Upload a Google service account JSON file.")
                changes.update(chromeos_workspace_domain=domain, chromeos_session_days=days)
            cleaned.update(changes)
            kerberos_enabled = request.form.get("kerberos_auto_login") == "1"
            kerberos_changes = {"kerberos_auto_login": "1" if kerberos_enabled else "0"}
            if kerberos_enabled:
                realm = str(request.form.get("kerberos_realm") or "").strip().upper()
                kdc = str(request.form.get("kerberos_kdc") or "").strip()
                spn = str(request.form.get("kerberos_spn") or "").strip()
                if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,254}", realm):
                    errors.append("Enter a valid Kerberos realm.")
                if not re.fullmatch(r"[A-Za-z0-9._:\-\[\]]{1,255}", kdc):
                    errors.append("Enter a valid KDC host name or address.")
                if len(spn) > 512 or any(character.isspace() for character in spn) or "/" not in spn or "@" in spn:
                    errors.append("Enter a valid SPN such as HTTP/paging.example.com.")
                if not KEYTAB_FILE.is_file():
                    errors.append("Upload a Kerberos service keytab.")
                elif realm and spn:
                    try:
                        validate_keytab(f"{spn}@{realm}")
                    except Exception:
                        errors.append("The service keytab does not contain the configured SPN.")
                kerberos_changes.update(kerberos_realm=realm, kerberos_kdc=kdc, kerberos_spn=spn)
                if not errors:
                    try:
                        write_krb5_config(realm, kdc)
                    except OSError:
                        errors.append("OPS could not write the Kerberos configuration.")
            cleaned.update(kerberos_changes)
        if errors:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(status="error", message=" ".join(errors))
            return page("Identity Settings", h(" ".join(errors)), "settings", user)
        cleaned["identity_setup_complete"] = "1"
        cleaned[LDAP_ROLE_MAPPING_SETTING] = json.dumps(cleaned[LDAP_ROLE_MAPPING_SETTING], separators=(",", ":"))
        return _save_identity_settings(
            cleaned,
            delete_ldap=delete_ldap,
            initial_sync=request.args.get("setup") == "wizard",
        )

    ctx = legacy_user_context(user)
    mode = request.args.get("setup")
    configured = (
        identity_data.get("identity_setup_complete") == "1"
        or identity_data.get("ldap_enabled") == "1"
        or identity_data.get("identity_provider") in REDIRECT_IDENTITY_PROVIDER_VALUES
    )
    if not configured and mode not in {"wizard", "manual"}:
        demo_wizard_action = ""
        if demo_mode_enabled():
            demo_wizard_action = (
                ' onclick="if(window.openDemoModePopup){openDemoModePopup(\'settings\');return false;}"'
                ' href="/dashboard?demomodal=settings"'
            )
        welcome = IDENTITY_WELCOME.replace(
            "__WIZARD_LINK_ATTRIBUTES__",
            (demo_wizard_action or ' href="?setup=wizard"').strip(),
        )
        return settings_page("Identity Settings", ctx, "identity", welcome, "")
    wizard = mode == "wizard" and not configured
    wizard_buttons = """
        <button type="button" id="identityWizardBack" hidden>Back</button>
        <button type="button" id="identityWizardNext">Next</button>
    """ if wizard else ""
    provider = identity_data.get("identity_provider", "local")
    group_rows = _group_option_rows()
    message_rows = _message_option_rows()
    bell_schedule_rows = _bell_schedule_option_rows()
    role_mapping_state = identity_data.get("ldap_role_mappings") or []
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

    protocol_choices = [("oidc", "OpenID Connect (OIDC)"), ("saml", "SAML")]
    if not prohibited:
        protocol_choices.insert(0, ("local", "Disabled"))
    provider_options = ""
    if prohibited and not wizard and provider == "local":
        provider_options = '<option value="" selected disabled>Choose a single sign-on protocol</option>'
    provider_options += "".join(_select_option_html(provider, value, label) for value, label in protocol_choices)
    wizard_markup = IDENTITY_WIZARD.replace(
        '<label id="wizardLocalSignIn"><input', '<label id="wizardLocalSignIn" hidden><input disabled'
    ) if prohibited else IDENTITY_WIZARD
    failure_options = "".join(
        _select_option_html(identity_data.get("ldap_failure_behavior", "deny"), value, label)
        for value, label in (("deny", "Deny"), ("fallback", "Local Fallback"))
    )
    body = f"""
    <style>
        #identitySettingsForm [hidden] {{ display:none !important; }}
        #identitySettingsForm .switch input:focus-visible + .slider {{ outline:2px solid var(--ops-accent); outline-offset:4px; }}
        .identity-wizard-choices {{ display:grid; gap:10px; border:0; margin:0; padding:0; }}
        .identity-wizard-choices legend {{ margin-bottom:8px; font-weight:500; }}
        #identityWizardError {{ color:var(--ops-error, #c62828); margin-top:12px; }}
        #identitySettingsForm .identity-actions {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:20px; }}
        #identitySettingsForm button.identity-delete-button {{ background:#c62828; color:#fff; }}
        #identitySettingsForm button.identity-delete-button:hover {{ background:#b71c1c; }}
        #identitySettingsForm #resetIdentityBtn {{ background:#b3261e; color:#fff; }}
        #identitySettingsForm #resetIdentityBtn:hover {{ background:#8c1d18; }}
        #identitySettingsForm .identity-use-wizard {{
            display:inline-flex; align-items:center; gap:8px; padding:10px 16px;
            border-radius:6px; background:#216e39; color:#fff; text-decoration:none; font-size:14px;
        }}
        #identitySettingsForm .identity-use-wizard:hover {{ background:#17572c; }}
        #identityLdapConnectionFailure {{
            --dialog-surface:#fff8f5;
            --dialog-detail:#f4e8e3;
            --dialog-text:#251915;
            --dialog-primary:#923f2b;
            --dialog-on-primary:#fff;
            --dialog-outline:#89736a;
            --dialog-state:#923f2b1a;
            color-scheme:light;
            box-sizing:border-box;
            width:min(520px, calc(100% - 32px));
            max-height:85vh;
            overflow:auto;
            border:0;
            border-radius:28px;
            padding:24px;
            background:var(--dialog-surface);
            color:var(--dialog-text);
            box-shadow:0 8px 24px #0004;
        }}
        @media (prefers-color-scheme:dark) {{
            #identityLdapConnectionFailure {{
                --dialog-surface:#242126;
                --dialog-detail:#302c34;
                --dialog-text:#eee7f1;
                --dialog-primary:var(--ops-accent);
                --dialog-on-primary:#fff;
                --dialog-outline:#EBC9C0;
                --dialog-state:rgba(200,101,76,.12);
                color-scheme:dark;
            }}
        }}
        #identityLdapConnectionFailure::backdrop {{ background:#0009; }}
        #identityLdapConnectionFailure h2 {{ color:inherit; margin:0 0 16px; font-size:1.5rem; }}
        #identityLdapConnectionFailure p {{ color:inherit; line-height:1.5; }}
        #identityLdapFailureDetail {{
            background:var(--dialog-detail);
            border-radius:12px;
            padding:12px;
            max-height:35vh;
            overflow:auto;
            white-space:pre-wrap;
            overflow-wrap:anywhere;
        }}
        #identitySettingsForm .identity-dialog-actions {{ justify-content:flex-end; }}
        #identityLdapConnectionFailure button {{
            min-height:44px;
            border:1px solid transparent;
            border-radius:999px;
            padding:8px 20px;
            font:inherit;
            font-size:0.875rem;
            font-weight:600;
        }}
        #identityLdapConnectionFailure .identity-dialog-filled {{
            background:var(--dialog-primary);
            color:var(--dialog-on-primary);
        }}
        #identityLdapConnectionFailure .identity-dialog-filled:hover {{ box-shadow:0 2px 6px #0005; }}
        #identityLdapConnectionFailure .identity-dialog-outlined {{
            background:transparent;
            border-color:var(--dialog-outline);
            color:var(--dialog-primary);
        }}
        #identityLdapConnectionFailure .identity-dialog-outlined:hover {{ background:var(--dialog-state); }}
        #identityLdapConnectionFailure button:focus-visible {{ outline:2px solid var(--dialog-primary); outline-offset:3px; }}
        #identityWizardBack {{ background:transparent; color:var(--ops-accent); border:1px solid currentColor; }}
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
    <div id="identity" class="tab-content active">
        <div class="info-card login-settings">
            <form id="identitySettingsForm">
                {wizard_markup if wizard else ""}
                <section aria-labelledby="ldapSettingsTitle"{" hidden" if wizard else ""}>
                    <h4 id="ldapSettingsTitle">User federation</h4>
                    <p>Sync users from a central source</p>
                    <div class="info-row">
                        <label class="info-label" id="ldapEnableLabel" for="ldapEnabled">Synchronization with LDAP</label>
                        <label class="switch"><input type="checkbox" role="switch" name="ldap_enabled" id="ldapEnabled"{" disabled" if ldap_locked else ""} aria-labelledby="ldapEnableLabel"{" checked" if identity_data.get("ldap_enabled") == "1" else ""}><span class="slider"></span></label>
                    </div>
                    {'<input type="hidden" name="ldap_enabled" value="1"><p>Disabling LDAP syncing is prohibited by the server configuration.</p>' if ldap_locked else ""}
                    <div id="ldapIdentityFields" style="display:none;">
                        <details class="ldap-settings-dropdown" id="ldapSettingsDropdown" open>
                            <summary>Directory connection and group mapping</summary>
                            <div class="ldap-settings-panel" id="ldapConnectionFields">
                                <div id="ldapTemplateRow" class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <input type="hidden" name="ldap_template" id="ldapTemplate" value="{h(identity_data.get("ldap_template", "generic"))}">
                                    <label class="info-label" for="ldapTemplatePicker">Directory template</label>
                                    <select id="ldapTemplatePicker">
                                        <option value="" selected hidden>Template</option>
                                        <option value="generic">Generic LDAP</option>
                                        <option value="openldap">OpenLDAP</option>
                                        <option value="active-directory">Active Directory</option>
                                    </select>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapServerAddress">Server address</label>
                                    <input type="text" name="ldap_server_address" id="ldapServerAddress" value="{h(identity_data.get("ldap_server_address", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapServerPort">Server port</label>
                                    <input type="number" name="ldap_server_port" id="ldapServerPort" min="1" max="65535" value="{h(identity_data.get("ldap_server_port", "389"))}">
                                </div>
                                <div class="info-row">
                                    <label class="info-label" for="ldapSecure">Secure LDAP</label>
                                    <span><label class="switch"><input type="checkbox" name="ldap_secure" id="ldapSecure"{" checked" if identity_data.get("ldap_secure") == "1" else ""}><span class="slider"></span></label></span>
                                </div>
                                <div id="ldapCaCertificateRow" class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;"{"" if identity_data.get("ldap_secure") == "1" else " hidden"}>
                                    <label class="info-label" for="ldapCaCertificate">Additional CA certificate</label>
                                    <textarea name="ldap_ca_certificate" id="ldapCaCertificate">{h(identity_data.get("ldap_ca_certificate", ""))}</textarea>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapBaseDn">Base DN</label>
                                    <input type="text" name="ldap_base_dn" id="ldapBaseDn" value="{h(identity_data.get("ldap_base_dn", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapBindUsername">Bind username</label>
                                    <input type="text" name="ldap_bind_username" id="ldapBindUsername" value="{h(identity_data.get("ldap_bind_username", ""))}" autocomplete="off">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapBindPassword">Bind password</label>
                                    <input type="password" name="ldap_bind_password" id="ldapBindPassword" value="" autocomplete="off" data-secret-saved="{int(bool(identity_data.get("ldap_bind_password")))}" placeholder="Leave blank to keep current bind password">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapPasswordChangeUrl">Password change URL</label>
                                    <input type="text" name="ldap_password_change_url" id="ldapPasswordChangeUrl" value="{h(identity_data.get("ldap_password_change_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapLoginField">Login field</label>
                                    <input type="text" name="ldap_login_field" id="ldapLoginField" value="{h(identity_data.get("ldap_login_field", "uid"))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapUserSearchFilter">User search filter</label>
                                    <input type="text" name="ldap_user_search_filter" id="ldapUserSearchFilter" value="{h(identity_data.get("ldap_user_search_filter", "({field}={username})"))}">
                                    <span class="info-description">Sync imports every entry this filter returns. If the filter contains <code>{{username}}</code>, sync substitutes a wildcard.</span>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapDisplayNameField">Display name field</label>
                                    <input type="text" name="ldap_display_name_field" id="ldapDisplayNameField" value="{h(identity_data.get("ldap_display_name_field", "cn"))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapEmailField">Email field</label>
                                    <input type="text" name="ldap_email_field" id="ldapEmailField" value="{h(identity_data.get("ldap_email_field", "mail"))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapDirectoryAccess">Directory Access</label>
                                    <select name="ldap_directory_access" id="ldapDirectoryAccess">
                                        {_select_option_html(identity_data["ldap_directory_access"], "read_write", "Read+Write")}
                                        {_select_option_html(identity_data["ldap_directory_access"], "read_only", "Read Only")}
                                    </select>
                                    <span class="info-description">In-app password changes require secure LDAP and directory permission to change the user’s password.</span>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapPasswordChangeMethod">Password change method</label>
                                    <select name="ldap_password_change_method" id="ldapPasswordChangeMethod">
                                        {_select_option_html(identity_data["ldap_password_change_method"], "standard", "LDAP Password Modify")}
                                        {_select_option_html(identity_data["ldap_password_change_method"], "active_directory", "Active Directory")}
                                    </select>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldap_disabled_attribute">Account deactivation attribute</label>
                                    <input type="text" name="ldap_disabled_attribute" id="ldap_disabled_attribute" value="{h(identity_data["ldap_disabled_attribute"])}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldap_disabled_rule">Account deactivation rule</label>
                                    <input type="text" name="ldap_disabled_rule" id="ldap_disabled_rule" value="{h(identity_data["ldap_disabled_rule"])}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldap_password_expired_attribute">Password expiration attribute</label>
                                    <input type="text" name="ldap_password_expired_attribute" id="ldap_password_expired_attribute" value="{h(identity_data["ldap_password_expired_attribute"])}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldap_password_expired_rule">Password expiration rule</label>
                                    <input type="text" name="ldap_password_expired_rule" id="ldap_password_expired_rule" value="{h(identity_data["ldap_password_expired_rule"])}">
                                </div>
                                <p>Rules accept comma-separated values, <code>present</code>, <code>mask:2</code>, <code>before-now</code>, or <code>age:90</code> (days since the attribute date). Enter <code>none</code> to turn a rule off.</p>
                                <div class="info-row">
                                    <span class="info-label">Local login fallback</span>
                                    <span><label class="switch"><input type="checkbox" name="ldap_local_login_fallback" id="ldapLocalLoginFallback"{" checked" if identity_data.get("ldap_local_login_fallback") == "1" else ""}><span class="slider"></span></label></span>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapConnectionTimeout">Connection timeout</label>
                                    <input type="number" name="ldap_connection_timeout" id="ldapConnectionTimeout" min="1" max="120" value="{h(identity_data.get("ldap_connection_timeout", "5"))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapSyncInterval">Sync interval (seconds)</label>
                                    <input type="number" name="ldap_sync_interval" id="ldapSyncInterval" min="1" max="86400" value="{h(identity_data.get("ldap_sync_interval", "60"))}">
                                    <span class="info-description">How often to sync directory users automatically. Default is 60 seconds.</span>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="ldapFailureBehavior">Failure behavior</label>
                                    <select name="ldap_failure_behavior" id="ldapFailureBehavior">
                                        {failure_options}
                                    </select>
                                </div>
                                <div id="ldapGroupMappingRow" class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <span class="info-label">Directory group to role mapping</span>
                                    <span class="info-description">The first matching rule applies. The Other/Unmatched rule controls access for users whose groups do not match.</span>
                                    <input type="hidden" name="{LDAP_ROLE_MAPPING_SETTING}" id="ldapRoleMappingsJson">
                                    <div id="ldapRoleMappingsList" class="ldap-role-mapping-list"></div>
                                    <button type="button" id="ldapAddRoleMappingBtn" class="ldap-role-mapping-add"><i class="fa-solid fa-plus"></i> Add Role Mapping</button>
                                </div>
                            </div>
                        </details>
                    </div>
                    <div id="ldapSyncActions" style="margin-top:20px;"{"" if identity_data.get("ldap_enabled") == "1" else " hidden"}>
                        <button type="button" id="syncLdapUsers">Sync LDAP Users</button>
                        <span id="ldapSyncStatus" class="save-status" role="status"></span>
                        <p id="ldapSyncActivity" class="info-description">Waiting for the next LDAP sync.</p>
                    </div>
                    {'<label id="ldapDeleteOption" hidden><input type="checkbox" name="delete_ldap_users" value="1" disabled> Delete LDAP Users</label>' if not prohibited else ""}
                </section>
                <section style="margin-top:24px;" aria-labelledby="ssoSettingsTitle"{" hidden" if wizard else ""}>
                <h4 id="ssoSettingsTitle">Single sign-on</h4>
                <p>Authenticate users with an external identity provider. The username or email must either exist locally or be synced using federation.</p>
                <div id="identityProtocolRow" class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px; border-bottom: none; padding-bottom: 0;">
                    <select name="identity_provider" id="identityProvider" aria-label="Sign-on protocol">
                        {provider_options}
                    </select>
                </div>
                <div class="settings-section-divider">
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
                    <div id="oidcIdentityFields" style="display:none;">
                        <details class="ldap-settings-dropdown" id="oidcSettingsDropdown">
                            <summary>OIDC Settings</summary>
                            <div class="ldap-settings-panel">
                                <p>Register this callback with your provider: <code>{h(request.url_root.rstrip("/") + "/login/oidc/callback")}</code></p>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="oidcDiscoveryUrl">Discovery URL</label>
                                    <input type="text" name="oidc_discovery_url" id="oidcDiscoveryUrl" value="{h(identity_data.get("oidc_discovery_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="oidcClientId">Client ID</label>
                                    <input type="text" name="oidc_client_id" id="oidcClientId" value="{h(identity_data.get("oidc_client_id", ""))}" autocomplete="off">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="oidcClientSecret">Client secret</label>
                                    <input type="password" name="oidc_client_secret" id="oidcClientSecret" value="" autocomplete="off" data-secret-saved="{int(bool(identity_data.get("oidc_client_secret")))}" placeholder="Leave blank to keep current client secret">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="oidcPasswordChangeUrl">Password change URL</label>
                                    <input type="text" name="oidc_password_change_url" id="oidcPasswordChangeUrl" value="{h(identity_data.get("oidc_password_change_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="oidcScope">Scope</label>
                                    <input type="text" name="oidc_scope" id="oidcScope" value="{h(identity_data.get("oidc_scope", OIDC_SETTING_DEFAULTS['oidc_scope']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="oidcUsernameClaim">Username claim</label>
                                    <input type="text" name="oidc_username_claim" id="oidcUsernameClaim" value="{h(identity_data.get("oidc_username_claim", OIDC_SETTING_DEFAULTS['oidc_username_claim']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="oidc_email_claim">Verified email claim</label>
                                    <input type="text" name="oidc_email_claim" id="oidc_email_claim" value="{h(identity_data["oidc_email_claim"])}">
                                </div>
                            </div>
                        </details>
                    </div>
                    <div id="samlIdentityFields" style="display:none;">
                        <details class="ldap-settings-dropdown" id="samlSettingsDropdown">
                            <summary>SAML Settings</summary>
                            <div class="ldap-settings-panel">
                                <p>Use <a href="/login/saml/metadata">SP metadata</a> after saving, or register <code>{h(request.url_root.rstrip("/") + "/login/saml/callback")}</code> as the assertion consumer URL.</p>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="samlIdpEntityId">IdP entity ID</label>
                                    <input type="text" name="saml_idp_entity_id" id="samlIdpEntityId" value="{h(identity_data.get("saml_idp_entity_id", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="samlSsoUrl">SSO URL</label>
                                    <input type="text" name="saml_sso_url" id="samlSsoUrl" value="{h(identity_data.get("saml_sso_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="samlX509Certificate">X.509 certificate</label>
                                    <textarea name="saml_x509_certificate" id="samlX509Certificate">{h(identity_data.get("saml_x509_certificate", ""))}</textarea>
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="samlPasswordChangeUrl">Password change URL</label>
                                    <input type="text" name="saml_password_change_url" id="samlPasswordChangeUrl" value="{h(identity_data.get("saml_password_change_url", ""))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="samlUsernameAttribute">Username attribute</label>
                                    <input type="text" name="saml_username_attribute" id="samlUsernameAttribute" value="{h(identity_data.get("saml_username_attribute", SAML_SETTING_DEFAULTS['saml_username_attribute']))}">
                                </div>
                                <div class="info-row" style="flex-direction: column; align-items: flex-start; gap: 8px;">
                                    <label class="info-label" for="saml_email_attribute">Email attribute</label>
                                    <input type="text" name="saml_email_attribute" id="saml_email_attribute" value="{h(identity_data["saml_email_attribute"])}">
                                </div>
                            </div>
                        </details>
                    </div>
                </div>
                </section>
                {desktop_auto_login_section(data) if not wizard else ""}
                <div class="identity-actions">
                    {wizard_buttons}
                    <button type="button" id="saveIdentityBtn"{" hidden" if wizard else ""}>{"Save identity setup" if wizard else "Save Settings"}</button>
                    {'<a class="identity-use-wizard" href="/admin/settings/identity"><i class="fa-solid fa-wand-magic-sparkles" aria-hidden="true"></i> Use Wizard</a>' if not wizard and not configured else ""}
                    {'<button type="button" id="resetIdentityBtn">Reset to Defaults</button>' if configured and not prohibited else ""}
                    <span id="save-status" class="save-status" role="status"></span>
                </div>
                <p id="resetIdentityDetail" role="status" hidden></p>
            </form>
        </div>
    </div>"""
    script = r"""
document.addEventListener('DOMContentLoaded', function() {
    const chromeosAutoLogin = document.getElementById('chromeosAutoLogin');
    chromeosAutoLogin?.addEventListener('change', () => {
        document.getElementById('chromeosSettingsFields').hidden = !chromeosAutoLogin.checked;
    });
    if (chromeosAutoLogin) {
        const file = document.getElementById('chromeosServiceAccountFile');
        const upload = document.getElementById('chromeosUploadKey');
        const remove = document.getElementById('chromeosDeleteKey');
        const status = document.getElementById('chromeosKeyStatus');
        async function updateKey(action) {
            const data = new FormData();
            data.append('chromeos_key_action', action);
            data.append('chromeos_settings_csrf', chromeosAutoLogin.form.elements.chromeos_settings_csrf.value);
            if (action === 'upload') data.append('chromeos_service_account_json', file.files[0]);
            upload.disabled = remove.disabled = true;
            status.textContent = action === 'upload' ? 'Uploading…' : 'Deleting…';
            try {
                const response = await fetch('/admin/settings/identity', {
                    method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}
                });
                const result = await response.json();
                if (!response.ok || result.status !== 'success') {
                    status.textContent = result.message || 'Could not update the service account file.';
                    return;
                }
                upload.hidden = result.uploaded;
                remove.hidden = !result.uploaded;
                status.textContent = result.uploaded ? 'File uploaded.' : 'File deleted.';
            } catch (_) {
                status.textContent = 'Could not update the service account file.';
            } finally {
                file.value = '';
                upload.disabled = remove.disabled = false;
            }
        }
        upload.addEventListener('click', () => file.click());
        file.addEventListener('change', () => { if (file.files.length) updateKey('upload'); });
        remove.addEventListener('click', () => updateKey('delete'));
    }
    const kerberosAutoLogin = document.getElementById('kerberosAutoLogin');
    kerberosAutoLogin?.addEventListener('change', () => {
        document.getElementById('kerberosSettingsFields').hidden = !kerberosAutoLogin.checked;
    });
    if (kerberosAutoLogin) {
        const file = document.getElementById('kerberosKeytabFile');
        const upload = document.getElementById('kerberosUploadKeytab');
        const remove = document.getElementById('kerberosDeleteKeytab');
        const status = document.getElementById('kerberosKeytabStatus');
        async function updateKeytab(action) {
            const data = new FormData();
            data.append('kerberos_key_action', action);
            data.append('chromeos_settings_csrf', kerberosAutoLogin.form.elements.chromeos_settings_csrf.value);
            data.append('kerberos_spn', document.getElementById('kerberosSpn').value.trim());
            data.append('kerberos_realm', document.getElementById('kerberosRealm').value.trim());
            if (action === 'upload') data.append('kerberos_service_keytab', file.files[0]);
            upload.disabled = remove.disabled = true;
            status.textContent = action === 'upload' ? 'Uploading…' : 'Deleting…';
            try {
                const response = await fetch('/admin/settings/identity', {
                    method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}
                });
                const result = await response.json();
                if (!response.ok || result.status !== 'success') {
                    status.textContent = result.message || 'Could not update the service keytab.';
                    return;
                }
                upload.hidden = result.uploaded;
                remove.hidden = !result.uploaded;
                status.textContent = result.uploaded ? 'Keytab uploaded.' : 'Keytab deleted.';
            } catch (_) {
                status.textContent = 'Could not update the service keytab.';
            } finally {
                file.value = '';
                upload.disabled = remove.disabled = false;
            }
        }
        upload.addEventListener('click', () => {
            if (!document.getElementById('kerberosSpn').value.trim() || !document.getElementById('kerberosRealm').value.trim()) {
                status.textContent = 'Enter the realm and SPN before uploading its keytab.';
                return;
            }
            file.click();
        });
        file.addEventListener('change', () => { if (file.files.length) updateKeytab('upload'); });
        remove.addEventListener('click', () => updateKeytab('delete'));
    }
    const ldapEnabled = document.getElementById('ldapEnabled');
    const ldapSyncActions = document.getElementById('ldapSyncActions');
    const ldapDeleteOption = document.getElementById('ldapDeleteOption');
    const ldapWasEnabled = !ldapSyncActions.hidden;
    const ldapStatusTemplates = __LDAP_STATUS_TEMPLATES__;
    const identityProvider = document.getElementById('identityProvider');
    const ssoIdentityOptions = document.getElementById('ssoIdentityOptions');
    const ldapIdentityFields = document.getElementById('ldapIdentityFields');
    const oidcIdentityFields = document.getElementById('oidcIdentityFields');
    const samlIdentityFields = document.getElementById('samlIdentityFields');
    const wizardNext = document.getElementById('identityWizardNext');
    const ldapSettingsDropdown = document.getElementById('ldapSettingsDropdown');
    const oidcSettingsDropdown = document.getElementById('oidcSettingsDropdown');
    const samlSettingsDropdown = document.getElementById('samlSettingsDropdown');
    const ldapTemplate = document.getElementById('ldapTemplate');
    const ldapTemplatePicker = document.getElementById('ldapTemplatePicker');
    const ldapSecure = document.getElementById('ldapSecure');
    const ldapCaCertificateRow = document.getElementById('ldapCaCertificateRow');
    const ldapServerPort = document.getElementById('ldapServerPort');
    const ldapLoginField = document.getElementById('ldapLoginField');
    const ldapUserSearchFilter = document.getElementById('ldapUserSearchFilter');
    const ldapDisplayNameField = document.getElementById('ldapDisplayNameField');
    const ldapEmailField = document.getElementById('ldapEmailField');
    const ldapRoleMappingsInput = document.getElementById('ldapRoleMappingsJson');
    const ldapRoleMappingsList = document.getElementById('ldapRoleMappingsList');
    const ldapAddRoleMappingBtn = document.getElementById('ldapAddRoleMappingBtn');
    const saveIdentityBtn = document.getElementById('saveIdentityBtn');
    const roleMappingPermissionSections = __OPS_ROLE_MAPPING_PERMISSION_SECTIONS__;
    const roleMappingPermissionLabels = __OPS_ROLE_MAPPING_PERMISSION_LABELS__;
    const roleMappingGroupOptions = __OPS_ROLE_MAPPING_GROUP_OPTIONS__;
    const roleMappingMessageOptions = __OPS_ROLE_MAPPING_MESSAGE_OPTIONS__;
    const roleMappingBellScheduleOptions = __OPS_ROLE_MAPPING_BELL_SCHEDULE_OPTIONS__;
    const roleMappingDefaultPermissions = __OPS_ROLE_MAPPING_DEFAULT_PERMISSIONS__;
    const initialLdapRoleMappings = __OPS_LDAP_ROLE_MAPPINGS__;

    const ldapTemplates = {
        generic: {
            port: '389',
            securePort: '636',
            loginField: 'uid',
            searchFilter: '({field}={username})',
            displayNameField: 'cn',
            emailField: 'mail'
        },
        openldap: {
            port: '389',
            securePort: '636',
            loginField: 'uid',
            searchFilter: '(&(objectClass=inetOrgPerson)({field}={username}))',
            displayNameField: 'cn',
            emailField: 'mail'
        },
        'active-directory': {
            port: '389',
            securePort: '636',
            loginField: 'sAMAccountName',
            searchFilter: '(&(objectCategory=person)(objectClass=user)({field}={username}))',
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
                        '<input type="text" data-role-mapping-group-match value="' + escapeHtml(mapping.group_match) + '" placeholder="Group name">' +
                      '</div>' +
                      (mapping.claim_match ? '<div class="ldap-role-mapping-field">' +
                        '<span class="info-label">Legacy claim condition</span>' +
                        '<textarea data-role-mapping-claim-match rows="1" placeholder="any" style="height:42px;min-height:42px;max-height:42px;resize:none;overflow:auto;">' + escapeHtml(mapping.claim_match) + '</textarea>' +
                      '</div>' : '');
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

    function syncIdentityProvider(userChanged) {
        const value = identityProvider ? identityProvider.value : 'local';
        const isLdap = ldapEnabled.checked;
        ldapSyncActions.hidden = !ldapWasEnabled || !isLdap;
        if (ldapDeleteOption) {
            const checkbox = ldapDeleteOption.querySelector('input');
            ldapDeleteOption.hidden = !ldapWasEnabled || isLdap;
            checkbox.disabled = ldapDeleteOption.hidden;
            if (checkbox.disabled) checkbox.checked = false;
        }
        const isOidc = value === 'oidc';
        const isSaml = value === 'saml';
        const isSso = isOidc || isSaml;
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
        if (force) {
            const statusFields = ldapStatusTemplates[ldapTemplate.value] || {};
            for (const name of ['ldap_disabled_attribute', 'ldap_disabled_rule', 'ldap_password_expired_attribute', 'ldap_password_expired_rule', 'ldap_password_change_method']) {
                document.getElementById(name === 'ldap_password_change_method' ? 'ldapPasswordChangeMethod' : name).value =
                    statusFields[name] || (name === 'ldap_password_change_method' ? 'standard' : '');
            }
        }
        syncLdapPort(force);
    }

    ldapEnabled.addEventListener('change', () => syncIdentityProvider(true));
    if (identityProvider) {
        identityProvider.addEventListener('change', function() { syncIdentityProvider(true); });
        syncIdentityProvider(false);
    }
    if (ldapTemplatePicker) {
        ldapTemplatePicker.addEventListener('change', function() {
            if (!ldapTemplate || !this.value) return;
            ldapTemplate.value = this.value;
            applyLdapTemplate(true);
            if (!wizardNext) this.value = '';
        });
    }
    if (ldapSecure) {
        ldapSecure.addEventListener('change', function() {
            syncLdapPort(true);
            ldapCaCertificateRow.hidden = !ldapSecure.checked;
        });
    }
    const ldapRoleMappingEditor = createRoleMappingEditor(ldapRoleMappingsList, ldapRoleMappingsInput, ldapAddRoleMappingBtn, initialLdapRoleMappings);
    if (saveIdentityBtn) {
        saveIdentityBtn.addEventListener('click', function() {
            ldapRoleMappingEditor.syncInput();
        });
    }
    ldapRoleMappingEditor.render();
    applyLdapTemplate(false);
    const form = document.getElementById('identitySettingsForm');
    form.addEventListener('submit', event => event.preventDefault());
    if (wizardNext) {
        const stepIds = [
            'wizardLdapChoice', 'wizardLdapTemplate', 'wizardLdapConnection', 'wizardLdapGroups',
            'wizardSsoChoice', 'wizardSsoProtocol', 'wizardSsoConnection', 'wizardSsoBehavior', 'wizardReview'
        ];
        const sections = Object.fromEntries(stepIds.map(id => [id, document.getElementById(id)]));
        function moveFields(step, ...fieldIds) {
            sections[step].append(...fieldIds.map(id => document.getElementById(id)));
        }
        moveFields('wizardLdapTemplate', 'ldapTemplateRow');
        moveFields('wizardLdapGroups', 'ldapGroupMappingRow');
        moveFields('wizardLdapConnection', 'ldapConnectionFields');
        moveFields('wizardSsoConnection', 'oidcIdentityFields', 'samlIdentityFields');

        const progress = document.getElementById('identityWizardProgress');
        const back = document.getElementById('identityWizardBack');
        const error = document.getElementById('identityWizardError');
        let current = stepIds[0];
        const signInChoices = form.querySelectorAll('[name="wizard_sign_in"]');
        const protocolChoices = form.querySelectorAll('[name="wizard_sso_protocol"]');
        const loginChoices = form.querySelectorAll('[name="wizard_login_behavior"]');
        const redirectAuto = document.getElementById('identityRedirectAuto');
        const allowLocal = document.getElementById('identityAllowLocalLogin');
        const selected = name => form.querySelector('[name="' + name + '"]:checked');
        function syncWizardSignIn() {
            const mode = selected('wizard_sign_in')?.value;
            identityProvider.value = mode === 'sso' ? selected('wizard_sso_protocol')?.value || '' : 'local';
            syncIdentityProvider(true);
            showStep(current, false);
        }
        for (const choice of [...signInChoices, ...protocolChoices]) choice.addEventListener('change', syncWizardSignIn);
        for (const choice of loginChoices) choice.addEventListener('change', () => {
            redirectAuto.checked = choice.value !== 'select';
            allowLocal.checked = choice.value !== 'require';
        });
        redirectAuto.checked = true;
        allowLocal.checked = false;

        function visibleSteps() {
            return stepIds.filter(id =>
                (!['wizardLdapTemplate', 'wizardLdapConnection', 'wizardLdapGroups'].includes(id) || ldapEnabled.checked) &&
                (!['wizardSsoProtocol', 'wizardSsoConnection', 'wizardSsoBehavior'].includes(id) || selected('wizard_sign_in')?.value === 'sso')
            );
        }

        function showStep(id, focusHeading) {
            current = id;
            const steps = visibleSteps();
            const index = steps.indexOf(current);
            const reviewing = current === 'wizardReview';
            for (const [name, section] of Object.entries(sections)) section.hidden = name !== current;
            back.hidden = index === 0;
            back.disabled = index === 0;
            wizardNext.hidden = reviewing;
            wizardNext.disabled = reviewing;
            saveIdentityBtn.hidden = !reviewing;
            progress.textContent = 'Step ' + (index + 1) + ' of ' + steps.length;
            error.hidden = true;
            document.getElementById('save-status').textContent = '';
            if (reviewing) {
                const directory = ldapEnabled.checked ? ldapTemplatePicker.selectedOptions[0].textContent : 'Disabled';
                const provider = identityProvider.value === 'local' ? 'Disabled' : identityProvider.selectedOptions[0].textContent;
                document.getElementById('identityReviewSummary').textContent =
                    'LDAP user syncing: ' + directory + '. Single sign-on: ' + provider + '.';
                const syncSummary = document.getElementById('identityReviewLdap');
                syncSummary.hidden = !ldapEnabled.checked;
                document.getElementById('identityReviewLdapInterval').textContent =
                    document.getElementById('ldapSyncInterval').value || '60';
                const login = document.getElementById('identityReviewLogin');
                login.hidden = identityProvider.value === 'local';
                login.textContent =
                    (document.getElementById('identityRedirectAuto').checked ? 'Redirect to the provider automatically. ' : 'Let users start SSO from the login page. ') +
                    (document.getElementById('identityAllowLocalLogin').checked ? 'Show the local sign-in option.' : 'Hide the local sign-in option by default.');
            }
            if (focusHeading) sections[current].querySelector('h2').focus();
        }

        function validateStep() {
            const fields = {
                wizardLdapTemplate: [['ldapTemplatePicker', 'Choose a directory template.']],
                wizardLdapConnection: [
                    ['ldapServerAddress', 'Enter the LDAP server address.'],
                    ['ldapBaseDn', 'Enter the base DN where user searches should start.']
                ],
                wizardSsoConnection: identityProvider.value === 'oidc' ? [
                    ['oidcDiscoveryUrl', 'Enter the OIDC discovery URL.'],
                    ['oidcClientId', 'Enter the OIDC client ID.'],
                    ['oidcClientSecret', 'Enter the OIDC client secret.']
                ] : [
                    ['samlIdpEntityId', 'Enter the SAML provider entity ID.'],
                    ['samlSsoUrl', 'Enter the SAML sign-on URL.'],
                    ['samlX509Certificate', 'Enter the SAML signing certificate.']
                ]
            };
            if (current === 'wizardSsoChoice' && !selected('wizard_sign_in')) {
                return [Array.from(signInChoices).find(choice => !choice.closest('label').hidden), 'Choose how users will sign in.'];
            }
            if (current === 'wizardSsoProtocol' && !selected('wizard_sso_protocol')) {
                return [protocolChoices[0], 'Choose your single sign-on protocol.'];
            }
            if (current === 'wizardLdapChoice' && !form.querySelector('[name="wizard_ldap_choice"]:checked')) {
                return [form.querySelector('[name="wizard_ldap_choice"]'), 'Choose whether to import directory users.'];
            }
            if (current === 'wizardLdapConnection' && document.getElementById('ldapBindUsername').value.trim()) {
                fields.wizardLdapConnection.push(['ldapBindPassword', 'Enter the password for the directory account.']);
            }
            for (const [id, message] of fields[current] || []) {
                const field = document.getElementById(id);
                if (!field.value.trim() && field.dataset.secretSaved !== '1') return [field, message];
            }
            for (const field of sections[current].querySelectorAll('input[type="number"]')) {
                if (!field.validity.valid) return [field, field.validationMessage];
            }
            if (current === 'wizardLdapGroups') {
                for (const field of sections[current].querySelectorAll('[data-role-mapping-group-match]')) {
                    const claim = field.closest('.ldap-role-mapping-card').querySelector('[data-role-mapping-claim-match]');
                    if (!field.value.trim() && !claim?.value.trim()) {
                        return [field, 'Enter a group name for each mapping, or remove the empty mapping.'];
                    }
                }
            }
            return null;
        }

        for (const choice of form.querySelectorAll('[name="wizard_ldap_choice"]')) {
            choice.addEventListener('change', () => {
                ldapEnabled.checked = choice.value === 'yes';
                syncIdentityProvider(true);
                showStep(current, false);
            });
        }
        identityProvider.addEventListener('change', () => showStep(current, false));
        const connectionStatus = document.getElementById('identityWizardConnectionStatus');
        const connectionFailure = document.getElementById('identityLdapConnectionFailure');
        const ldapConnectionStep = sections.wizardLdapConnection;
        let checking = false;

        document.getElementById('identityLdapReconfigure').addEventListener('click', () => {
            connectionFailure.close();
            document.getElementById('ldapServerAddress').focus();
        });
        document.getElementById('identityLdapContinue').addEventListener('click', () => {
            connectionFailure.close();
            showStep('wizardLdapGroups', true);
        });

        async function checkLdapConnection(target) {
            checking = true;
            back.disabled = true;
            wizardNext.disabled = true;
            ldapConnectionStep.inert = true;
            error.hidden = true;
            connectionStatus.textContent = 'Checking the LDAP connection…';
            connectionStatus.hidden = false;
            let message;
            try {
                const body = new URLSearchParams({test_ldap_connection: '1'});
                for (const [name, value] of new FormData(form)) {
                    if (name.startsWith('ldap_')) body.append(name, value);
                }
                const response = await fetch('/admin/settings/identity', {
                    method: 'POST',
                    headers: {'X-Requested-With': 'XMLHttpRequest'},
                    body
                });
                const result = await response.json();
                if (!response.ok || result.status !== 'success') {
                    message = result.message || 'The server returned HTTP ' + response.status + '.';
                }
            } catch (exception) {
                message = 'The connection check could not be completed. ' + exception.message;
            } finally {
                checking = false;
                back.disabled = false;
                wizardNext.disabled = false;
                ldapConnectionStep.inert = false;
                connectionStatus.hidden = true;
            }
            if (message) {
                document.getElementById('identityLdapFailureDetail').textContent = message;
                connectionFailure.showModal();
            } else {
                showStep(target, true);
            }
        }

        function navigate(offset) {
            if (checking || connectionFailure.open) return;
            const steps = visibleSteps();
            const target = steps[steps.indexOf(current) + offset];
            if (!target) return;
            const issue = offset > 0 ? validateStep() : null;
            if (issue) {
                const [field, message] = issue;
                error.textContent = message;
                error.hidden = false;
                const details = field.closest('details');
                if (details) details.open = true;
                field.focus();
                return;
            }
            if (offset > 0 && current === 'wizardLdapConnection') {
                checkLdapConnection(target);
            } else {
                showStep(target, true);
            }
        }
        back.addEventListener('click', () => navigate(-1));
        wizardNext.addEventListener('click', () => navigate(1));
        showStep(current, false);
    }
    postSettings('identitySettingsForm','saveIdentityBtn','save-status','Settings saved successfully.', true);
    const resetButton = document.getElementById('resetIdentityBtn');
    resetButton?.addEventListener('click', async () => {
        if (window.openDemoModePopup && document.querySelector('[data-demo-mode="1"]')) {
            openDemoModePopup('settings');
            return;
        }
        if (!window.confirm('Clear all synced users and reset SSO settings? This cannot be undone.')) return;
        const status = document.getElementById('save-status');
        const detail = document.getElementById('resetIdentityDetail');
        resetButton.disabled = true;
        status.textContent = 'Resetting…';
        detail.hidden = true;
        try {
            const response = await fetch('/admin/settings/identity', {
                method: 'POST',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
                body: new URLSearchParams({reset_identity: '1'})
            });
            if (response.redirected || response.status === 401) {
                throw new Error('Sign in as an administrator and try the reset again.');
            }
            if (!response.headers.get('content-type')?.includes('application/json')) {
                throw new Error('The server returned HTTP ' + response.status + ' without reset details.');
            }
            const result = await response.json();
            if (!response.ok || result.status !== 'success') {
                throw new Error(result.detail || (result.message !== 'Reset failed' && result.message) || 'The server could not complete the reset.');
            }
            window.location.assign('/admin/settings/identity');
        } catch (error) {
            status.textContent = 'Reset failed';
            detail.textContent = error.message;
            detail.hidden = false;
            resetButton.disabled = false;
        }
    });
    const syncButton = document.getElementById('syncLdapUsers');
    const syncActivity = document.getElementById('ldapSyncActivity');
    async function refreshSyncActivity() {
        if (ldapSyncActions.hidden) return;
        try {
            const response = await fetch('/admin/settings/identity?ldap_sync_status=1', {cache: 'no-store'});
            if (!response.ok) return;
            const {sync} = await response.json();
            if (!sync) return;
            const mode = sync.source === 'manual' ? 'manually' : 'automatically';
            if (sync.state === 'complete' && sync.finished_at) {
                const users = sync.count === 1 ? 'user' : 'users';
                const finished = new Date(sync.finished_at).toLocaleString();
                syncActivity.textContent = `Last synced ${sync.count} ${users} ${mode} at ${finished}.`;
            } else {
                syncActivity.textContent = sync.message || 'LDAP sync is running ' + mode + '…';
            }
        } catch {
            syncActivity.textContent = 'LDAP sync status is temporarily unavailable.';
        }
    }
    if (ldapWasEnabled) {
        refreshSyncActivity();
        setInterval(refreshSyncActivity, 5000);
    }
    syncButton.addEventListener('click', async () => {
        const status = document.getElementById('ldapSyncStatus');
        syncButton.disabled = true;
        status.textContent = 'Syncing directory users…';
        try {
            const response = await fetch('/admin/settings/identity', {
                method: 'POST',
                headers: {'X-Requested-With': 'XMLHttpRequest'},
                body: new URLSearchParams({sync_ldap_users: '1'})
            });
            const result = await response.json();
            status.textContent = result.message || 'LDAP sync failed.';
        } catch {
            status.textContent = 'LDAP sync failed.';
        } finally {
            syncButton.disabled = false;
        }
    });
});
"""
    script = script.replace("__LDAP_STATUS_TEMPLATES__", json.dumps(LDAP_STATUS_TEMPLATES))
    script = script.replace("__OPS_ROLE_MAPPING_PERMISSION_SECTIONS__", json.dumps(role_mapping_permission_sections).replace("<", r"\u003c"))
    script = script.replace("__OPS_ROLE_MAPPING_PERMISSION_LABELS__", json.dumps(dict(USER_PERMISSION_LABELS)).replace("<", r"\u003c"))
    script = script.replace("__OPS_ROLE_MAPPING_GROUP_OPTIONS__", json.dumps(role_mapping_group_options).replace("<", r"\u003c"))
    script = script.replace("__OPS_ROLE_MAPPING_MESSAGE_OPTIONS__", json.dumps(role_mapping_message_options).replace("<", r"\u003c"))
    script = script.replace("__OPS_ROLE_MAPPING_BELL_SCHEDULE_OPTIONS__", json.dumps(role_mapping_bell_schedule_options).replace("<", r"\u003c"))
    script = script.replace("__OPS_ROLE_MAPPING_DEFAULT_PERMISSIONS__", json.dumps(default_user_permission_values).replace("<", r"\u003c"))
    script = script.replace("__OPS_LDAP_ROLE_MAPPINGS__", json.dumps(role_mapping_state).replace("<", r"\u003c"))
    return settings_page("Identity Settings", ctx, "identity", body, script)
