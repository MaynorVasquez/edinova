# Copyright (c) 2025, Maynor Vasquez and Contributors
# See license.txt

from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from edinova.api.credential import get_config
from edinova.edinova.doctype.credential.credential import Credential, get_token_for_copy
from edinova.patches.v1_0.migrate_credential_token_to_password import (
	execute as migrate_token,
)


class TestCredential(FrappeTestCase):
	@patch("edinova.api.credential.frappe.get_doc")
	def test_get_config_reads_encrypted_token(self, get_doc):
		credentials = Mock(nit="1234567", url="https://example.test/orders")
		credentials.get_password.return_value = "secret-token"
		get_doc.return_value = credentials

		config = get_config()

		self.assertEqual(config["token"], "secret-token")
		credentials.get_password.assert_called_once_with("token")

	@patch("edinova.edinova.doctype.credential.credential.frappe.get_doc")
	@patch("edinova.edinova.doctype.credential.credential.frappe.only_for")
	def test_copy_token_requires_system_manager(self, only_for, get_doc):
		credentials = Mock()
		credentials.get_password.return_value = "secret-token"
		get_doc.return_value = credentials

		token = get_token_for_copy()

		self.assertEqual(token, "secret-token")
		only_for.assert_called_once_with("System Manager")
		credentials.get_password.assert_called_once_with(
			"token", raise_exception=False
		)

	def test_non_administrator_cannot_change_token(self):
		credentials = Credential({"doctype": "Credential", "token": "new-token"})
		credentials._doc_before_save = frappe._dict(token="********")

		with patch.object(
			frappe.local, "session", frappe._dict(user="manager@example.com")
		):
			with self.assertRaises(frappe.PermissionError):
				credentials.validate()

	def test_administrator_can_change_token(self):
		credentials = Credential({"doctype": "Credential", "token": "new-token"})
		credentials._doc_before_save = frappe._dict(token="********")

		with patch.object(
			frappe.local, "session", frappe._dict(user="Administrator")
		):
			credentials.validate()

	@patch(
		"edinova.patches.v1_0.migrate_credential_token_to_password."
		"set_encrypted_password"
	)
	@patch(
		"edinova.patches.v1_0.migrate_credential_token_to_password."
		"frappe.db.set_single_value"
	)
	@patch(
		"edinova.patches.v1_0.migrate_credential_token_to_password."
		"frappe.db.get_single_value",
		return_value="legacy-token",
	)
	def test_patch_encrypts_legacy_plaintext_token(
		self, get_single_value, set_single_value, set_password
	):

		migrate_token()

		get_single_value.assert_called_once_with("Credential", "token")
		set_password.assert_called_once_with(
			"Credential", "Credential", "legacy-token", "token"
		)
		set_single_value.assert_called_once_with(
			"Credential", "token", "*" * len("legacy-token")
		)

	@patch(
		"edinova.patches.v1_0.migrate_credential_token_to_password."
		"set_encrypted_password"
	)
	@patch(
		"edinova.patches.v1_0.migrate_credential_token_to_password."
		"frappe.db.set_single_value"
	)
	@patch(
		"edinova.patches.v1_0.migrate_credential_token_to_password."
		"frappe.db.get_single_value",
		return_value="********",
	)
	def test_patch_ignores_already_masked_token(
		self, get_single_value, set_single_value, set_password
	):

		migrate_token()

		get_single_value.assert_called_once_with("Credential", "token")
		set_password.assert_not_called()
		set_single_value.assert_not_called()
