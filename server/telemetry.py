"""Send traces to Azure Application Insights, so every agent run can be followed in the Azure portal.

Microsoft Agent Framework already describes its work in OpenTelemetry (a span per workflow, per step and
per model call, with timings and token counts). This only switches on the exporter. It is keyless like the
rest of the app: the managed identity signs in, so the resource can refuse plain connection-string writes.
Off unless APPLICATIONINSIGHTS_CONNECTION_STRING is set (local runs and tests send nothing).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("touchline.telemetry")


def setup() -> bool:
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        return False
    os.environ.setdefault("OTEL_SERVICE_NAME", "touchline")
    # the page's static files and health pings are noise next to the agents' work
    os.environ.setdefault("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "static,api/health")
    from agent_framework.observability import enable_instrumentation
    from azure.identity import AzureCliCredential, ManagedIdentityCredential
    from azure.monitor.opentelemetry import configure_azure_monitor

    managed = os.getenv("TOUCHLINE_AUTH") == "managed"
    credential = ManagedIdentityCredential(client_id=os.getenv("AZURE_CLIENT_ID")) if managed else AzureCliCredential()
    configure_azure_monitor(connection_string=conn, credential=credential, logger_name="touchline")
    enable_instrumentation()
    log.info("tracing to Application Insights")
    return True
