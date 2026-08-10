// Copyright (c) 2025, Maynor Vasquez and contributors
// For license information, please see license.txt

frappe.ui.form.on("Credential", {
	refresh(frm) {
		const is_administrator = frappe.session.user === "Administrator";
		frm.set_df_property("token", "read_only", !is_administrator);

		const can_copy =
			is_administrator || frappe.user.has_role("System Manager");
		if (!can_copy) return;

		frm.add_custom_button(__("Copiar token"), async () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Guarda los cambios antes de copiar el token."));
				return;
			}

			const { message: token } = await frappe.call({
				method:
					"edinova.edinova.doctype.credential.credential.get_token_for_copy",
				freeze: true,
				freeze_message: __("Obteniendo token…"),
			});
			if (token) {
				frappe.utils.copy_to_clipboard(token);
			}
		});
	},
});
