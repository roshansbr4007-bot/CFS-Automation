from rest_framework import authentication


class SessionAuthentication(authentication.SessionAuthentication):
    """Session auth that answers 401 (not 403) when no one is logged in."""

    def authenticate_header(self, request):
        return "Session"
