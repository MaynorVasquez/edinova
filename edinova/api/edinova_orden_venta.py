import frappe
import requests

from .credential import get_config

logger = frappe.logger("edinova", allow_site=True, file_count=10)

REQUEST_TIMEOUT = (5, 30)


class EdinovaAPIError(frappe.ValidationError):
	"""Raised when Walmart's API cannot provide a valid successful response."""


def get_ordenes_venta(fecha_inicio: str) -> list[dict]:
	"""Consulta la API de Walmart y devuelve sólo las órdenes cuyo proveedor coincide con el NIT configurado."""
	config = get_config()
	nit_filtro = config.get("nit")
	url = config.get("url")
	token = config.get("token")
	if not nit_filtro or not url or not token:
		raise EdinovaAPIError(
			"La configuración de Edinova requiere NIT, URL y token."
		)

	headers = {"Authorization": f"Bearer {token}"}
	params = {"Fecha": fecha_inicio}

	try:
		response = requests.get(
			url,
			headers=headers,
			params=params,
			timeout=REQUEST_TIMEOUT,
		)
		response.raise_for_status()
	except requests.exceptions.Timeout as exc:
		logger.exception("Timeout consultando la API de Edinova")
		raise EdinovaAPIError(
			"La API de Edinova no respondió dentro del tiempo permitido."
		) from exc
	except requests.exceptions.RequestException as exc:
		status = getattr(getattr(exc, "response", None), "status_code", None)
		detail = f" (HTTP {status})" if status else ""
		logger.exception("Error HTTP consultando la API de Edinova%s", detail)
		if status in (401, 403):
			raise EdinovaAPIError(
				"El token de Edinova fue rechazado o no tiene permisos. "
				"Actualícelo en la configuración de Credential."
			) from exc
		raise EdinovaAPIError(
			f"No se pudo consultar la API de Edinova{detail}."
		) from exc

	try:
		data = response.json()
	except (requests.exceptions.JSONDecodeError, ValueError) as exc:
		logger.exception("La API de Edinova devolvió JSON inválido")
		raise EdinovaAPIError(
			"La API de Edinova devolvió una respuesta que no es JSON válido."
		) from exc

	if not isinstance(data, dict):
		raise EdinovaAPIError("La respuesta de la API de Edinova no es un objeto JSON.")
	if data.get("success") is not True:
		api_message = str(
			data.get("message")
			or data.get("menssage")
			or data.get("error")
			or "sin detalle"
		)
		raise EdinovaAPIError(
			f"La API de Edinova rechazó la consulta: {api_message[:300]}"
		)

	groups = data.get("data", [])
	if not isinstance(groups, list):
		raise EdinovaAPIError("La API de Edinova devolvió 'data' con formato inválido.")

	ordenes_filtradas = []
	for group_index, grupo in enumerate(groups, start=1):
		if not isinstance(grupo, dict):
			raise EdinovaAPIError(
				f"El grupo #{group_index} de la respuesta tiene formato inválido."
			)
		purchase_orders = grupo.get("purchaseOrders", [])
		if not isinstance(purchase_orders, list):
			raise EdinovaAPIError(
				f"El grupo #{group_index} contiene 'purchaseOrders' inválido."
			)
		for orden in purchase_orders:
			if not isinstance(orden, dict):
				raise EdinovaAPIError(
					f"El grupo #{group_index} contiene una orden con formato inválido."
				)
			supplier = orden.get("supplier") or {}
			if not isinstance(supplier, dict):
				raise EdinovaAPIError(
					f"El grupo #{group_index} contiene un proveedor con formato inválido."
				)
			if str(supplier.get("nit") or "") == str(nit_filtro):
				ordenes_filtradas.append(orden)

	return ordenes_filtradas
