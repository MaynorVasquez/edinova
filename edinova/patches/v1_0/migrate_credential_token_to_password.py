import frappe
from frappe.utils.password import set_encrypted_password


def execute():
	"""Move the legacy plaintext token from tabSingles to encrypted storage."""
	legacy_token = frappe.db.get_single_value("Credential", "token")
	if not legacy_token or set(legacy_token) == {"*"}:
		return

	set_encrypted_password("Credential", "Credential", legacy_token, "token")
	frappe.db.set_single_value("Credential", "token", "*" * len(legacy_token))
