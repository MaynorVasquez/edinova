# Copyright (c) 2025, Maynor Vasquez and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class Credential(Document):
	def validate(self):
		if frappe.session.user != "Administrator" and self.has_value_changed("token"):
			frappe.throw(
				_("Solo el usuario Administrator puede cambiar el token."),
				frappe.PermissionError,
			)


@frappe.whitelist()
def get_token_for_copy() -> str:
	"""Return the decrypted token only to users allowed to manage credentials."""
	frappe.only_for("System Manager")
	credentials = frappe.get_doc("Credential", "Credential")
	token = credentials.get_password("token", raise_exception=False)
	if not token:
		frappe.throw(_("No hay un token configurado."))
	return token
