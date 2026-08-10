import frappe


def get_config():
    credentials = frappe.get_doc("Credential", "Credential")
    return {
        "nit": credentials.nit,
        "url": credentials.url,
        "token": credentials.get_password("token"),
    }
