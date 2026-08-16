"""Herramienta para el envío de correos electrónicos mediante SMTP nativo en JARVIS OS."""

from __future__ import annotations

import email.mime.multipart
import email.mime.text
import logging
import smtplib
from typing import Any

from jarvis_core.tools.base import Tool, ToolResult

logger = logging.getLogger("jarvis.email")


class SendEmailTool(Tool):
    """Permite a JARVIS enviar un correo electrónico por SMTP directamente desde el servidor central."""

    name = "send_email"
    description = (
        "Envía un correo electrónico a un destinatario utilizando las credenciales SMTP configuradas en el servidor. "
        "Ideal para recordatorios, alertas y notificaciones automáticas."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Dirección de correo de destino (opcional, usa la de por defecto si se omite)."},
            "subject": {"type": "string", "description": "Asunto del correo electrónico."},
            "body": {"type": "string", "description": "Cuerpo del mensaje en texto plano o HTML."},
        },
        "required": ["subject", "body"],
    }

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

    async def execute(
        self, subject: str, body: str, to: str = "", **kwargs: Any
    ) -> ToolResult:
        recipient = (to or self.default_to).strip()
        if not recipient:
            return ToolResult(
                "Error: No se especificó destinatario y no hay un correo destino por defecto configurado (JARVIS_EMAIL_TO).",
                is_error=True,
            )

        if not self.smtp_host or not self.smtp_user or not self.smtp_pass:
            return ToolResult(
                "Error: El servidor SMTP no está configurado en las variables de entorno "
                "(JARVIS_SMTP_HOST, JARVIS_SMTP_USER, JARVIS_SMTP_PASS).",
                is_error=True,
            )

        try:
            msg = email.mime.multipart.MIMEMultipart()
            msg["From"] = self.email_from
            msg["To"] = recipient
            msg["Subject"] = subject
            msg.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))

            if self.smtp_port == 465:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15) as server:
                    server.login(self.smtp_user, self.smtp_pass)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_pass)
                    server.send_message(msg)

            logger.info("Correo enviado exitosamente a %s (Asunto: %s)", recipient, subject)
            return ToolResult(f"✓ Correo enviado con éxito a {recipient}.")

        except Exception as exc:
            logger.error("Error al enviar correo vía SMTP (%s): %s", self.smtp_host, exc)
            return ToolResult(f"Error enviando correo SMTP: {exc}", is_error=True)
