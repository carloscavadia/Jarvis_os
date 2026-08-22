"""Herramienta para el envío de correos electrónicos mediante SMTP nativo en JARVIS OS."""

from __future__ import annotations

import asyncio
import email.mime.multipart
import email.mime.text
import logging
import smtplib
from typing import Any

from jarvis_core.tools.base import Tool, ToolResult

logger = logging.getLogger("jarvis.email")

_SMTP_TIMEOUT_SECONDS = 15


class SendEmailTool(Tool):
    """Permite a JARVIS enviar un correo electrónico por SMTP directamente desde el servidor central."""

    name = "send_email"
    description = (
        "Envía un correo electrónico en texto plano a un destinatario utilizando las "
        "credenciales SMTP configuradas en el servidor. Ideal para recordatorios, "
        "alertas y notificaciones automáticas."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Dirección de correo de destino (opcional, usa la de por defecto si se omite).",
            },
            "subject": {"type": "string", "description": "Asunto del correo electrónico."},
            "body": {"type": "string", "description": "Cuerpo del mensaje, en texto plano."},
        },
        "required": ["subject", "body"],
    }
    #: Un correo sale del servidor y llega a un tercero: es la única acción de
    #: escritura hacia fuera que no pasa por n8n, así que se somete a la misma
    #: aprobación en el HUD que `run_connector_action` o `install_package`.
    requires_confirmation = True

    def __init__(
        self,
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_pass: str = "",
        default_to: str = "",
        email_from: str = "",
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_pass = smtp_pass
        self.default_to = default_to
        self.email_from = email_from or smtp_user

    def _send(self, recipient: str, subject: str, body: str) -> None:
        """Diálogo SMTP completo. Síncrono a propósito: lo llama `run` en un hilo."""
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = self.email_from
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))

        if self.smtp_port == 465:
            with smtplib.SMTP_SSL(
                self.smtp_host, self.smtp_port, timeout=_SMTP_TIMEOUT_SECONDS
            ) as server:
                server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
        else:
            with smtplib.SMTP(
                self.smtp_host, self.smtp_port, timeout=_SMTP_TIMEOUT_SECONDS
            ) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)

    async def run(
        self, subject: str = "", body: str = "", to: str = "", **kwargs: Any
    ) -> ToolResult:
        recipient = (to or self.default_to).strip()
        if not recipient:
            return ToolResult(
                "Error: No se especificó destinatario y no hay un correo destino por "
                "defecto configurado (JARVIS_EMAIL_TO).",
                is_error=True,
            )

        if not self.smtp_host or not self.smtp_user or not self.smtp_pass:
            return ToolResult(
                "Error: El servidor SMTP no está configurado en las variables de entorno "
                "(JARVIS_SMTP_HOST, JARVIS_SMTP_USER, JARVIS_SMTP_PASS).",
                is_error=True,
            )

        try:
            # `smtplib` bloquea: un servidor lento congelaría el bucle de eventos
            # del gateway entero —WebSockets, voz y HUD incluidos— hasta 15 s.
            await asyncio.to_thread(self._send, recipient, subject, body)
        except Exception as exc:
            logger.error("Error al enviar correo vía SMTP (%s): %s", self.smtp_host, exc)
            return ToolResult(f"Error enviando correo SMTP: {exc}", is_error=True)

        logger.info("Correo enviado exitosamente a %s (Asunto: %s)", recipient, subject)
        return ToolResult(f"✓ Correo enviado con éxito a {recipient}.")
