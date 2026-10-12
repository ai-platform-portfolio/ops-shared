"""Register every handler in one package; deployment replaces the whole Function App."""

import azure.functions as func
from profile_sync.handlers import app as profile_sync

app = func.FunctionApp()
app.register_functions(profile_sync)
# Register additional Blueprints here so every deployment includes every handler.
