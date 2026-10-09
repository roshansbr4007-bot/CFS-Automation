from drf_spectacular.extensions import OpenApiAuthenticationExtension


class SessionAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents the session cookie used by apps.core.authentication.SessionAuthentication."""

    target_class = "apps.core.authentication.SessionAuthentication"
    name = "sessionAuth"
    priority = 1

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": "sessionid"}
