from unittest.mock import Mock, patch

import requests
from frappe.tests.utils import FrappeTestCase

from edinova.api.edinova_orden_venta import (
	REQUEST_TIMEOUT,
	EdinovaAPIError,
	get_ordenes_venta,
)


class TestGetOrdenesVenta(FrappeTestCase):
	def setUp(self):
		self.config = {
			"nit": "1234567",
			"url": "https://example.test/orders",
			"token": "secret-token",
		}

	@patch("edinova.api.edinova_orden_venta.get_config")
	@patch("edinova.api.edinova_orden_venta.requests.get")
	def test_returns_only_orders_for_configured_nit(self, request_get, get_config):
		get_config.return_value = self.config
		response = Mock()
		response.json.return_value = {
			"success": True,
			"data": [
				{
					"purchaseOrders": [
						{"id": "matching", "supplier": {"nit": 1234567}},
						{"id": "other", "supplier": {"nit": "9999999"}},
					]
				}
			],
		}
		request_get.return_value = response

		orders = get_ordenes_venta("2026-08-10")

		self.assertEqual(orders, [{"id": "matching", "supplier": {"nit": 1234567}}])
		request_get.assert_called_once_with(
			self.config["url"],
			headers={"Authorization": "Bearer secret-token"},
			params={"Fecha": "2026-08-10"},
			timeout=REQUEST_TIMEOUT,
		)
		response.raise_for_status.assert_called_once_with()

	@patch("edinova.api.edinova_orden_venta.get_config")
	@patch("edinova.api.edinova_orden_venta.requests.get")
	def test_timeout_is_not_reported_as_empty_result(self, request_get, get_config):
		get_config.return_value = self.config
		request_get.side_effect = requests.exceptions.Timeout()

		with self.assertRaisesRegex(EdinovaAPIError, "tiempo permitido"):
			get_ordenes_venta("2026-08-10")

	@patch("edinova.api.edinova_orden_venta.get_config")
	@patch("edinova.api.edinova_orden_venta.requests.get")
	def test_http_error_is_not_reported_as_empty_result(self, request_get, get_config):
		get_config.return_value = self.config
		response = Mock(status_code=401)
		error = requests.exceptions.HTTPError(response=response)
		request_get.return_value.raise_for_status.side_effect = error

		with self.assertRaisesRegex(EdinovaAPIError, "token.*rechazado"):
			get_ordenes_venta("2026-08-10")

	@patch("edinova.api.edinova_orden_venta.get_config")
	@patch("edinova.api.edinova_orden_venta.requests.get")
	def test_success_false_raises_api_error(self, request_get, get_config):
		get_config.return_value = self.config
		request_get.return_value.json.return_value = {
			"success": False,
			"message": "Token vencido",
		}

		with self.assertRaisesRegex(EdinovaAPIError, "Token vencido"):
			get_ordenes_venta("2026-08-10")

	@patch("edinova.api.edinova_orden_venta.get_config")
	@patch("edinova.api.edinova_orden_venta.requests.get")
	def test_invalid_json_raises_api_error(self, request_get, get_config):
		get_config.return_value = self.config
		request_get.return_value.json.side_effect = ValueError("invalid json")

		with self.assertRaisesRegex(EdinovaAPIError, "JSON válido"):
			get_ordenes_venta("2026-08-10")
