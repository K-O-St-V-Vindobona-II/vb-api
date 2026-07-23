ACTION_LABELS: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): "Anmeldung",
    ("POST", "/api/auth/logout"): "Abmeldung",
    ("POST", "/api/auth/google"): "Google-Anmeldung",
    ("POST", "/api/auth/google/link"): "Google-Konto verknüpft",
    ("DELETE", "/api/auth/google/link"): "Google-Konto getrennt",
    ("POST", "/api/auth/forgot-password"): "Passwort-Reset angefordert",
    ("POST", "/api/auth/reset-password"): "Passwort zurückgesetzt",
    ("POST", "/api/standesdb/members"): "Mitglied angelegt",
    ("POST", "/api/standesdb/contacts"): "Kontakt angelegt",
    ("POST", "/api/archive/upload"): "Datei hochgeladen",
    ("POST", "/api/p4x/admin/accounts"): "Konto angelegt",
    ("POST", "/api/p4x/admin/categories"): "Kategorie angelegt",
    ("POST", "/api/p4x/admin/category-filters"): "Filter angelegt",
    ("POST", "/api/standesdb/export"): "Export erstellt",
    ("POST", "/api/p4x/admin/fee-config"): "Beitragskonfiguration angelegt",
    ("POST", "/api/p4x/admin/summary"): "Abrechnung erstellt",
    ("POST", "/api/archive/dirs"): "Ordner erstellt",
}

SUBRESOURCE_PATTERNS: list[tuple[str, str, str]] = [
    ("POST", "/images", "Profilbild hochgeladen"),
    ("PUT", "/images/", "Profilbild bearbeitet"),
    ("DELETE", "/images/", "Profilbild gelöscht"),
    ("GET", "/download/", "Datei heruntergeladen (Thumbnail)"),
    ("GET", "/download", "Datei heruntergeladen"),
    ("PATCH", "/restore", "Wiederhergestellt"),
    ("POST", "/receive", "Dateien verschoben"),
    ("POST", "/comments", "Kommentar erstellt"),
    ("DELETE", "/comments/", "Kommentar gelöscht"),
    ("POST", "/import", "Transaktionen importiert"),
    ("POST", "/set-partner", "Partner zugeordnet"),
    ("POST", "/set-category-direct", "Kategorie zugeordnet"),
    ("DELETE", "/unset-category-direct", "Kategoriezuordnung entfernt"),
    ("POST", "/filter2direct", "Filter → Direkt konvertiert"),
]

ACTION_PATTERNS: list[tuple[str, str, str]] = [
    ("GET", "/api/standesdb/members/", "Mitglied angezeigt"),
    ("GET", "/api/standesdb/contacts/", "Kontakt angezeigt"),
    ("GET", "/api/archive/dirs/", "Verzeichnis angezeigt"),
    ("GET", "/api/archive/files/", "Datei angezeigt"),
    ("PUT", "/api/standesdb/members/", "Mitglied bearbeitet"),
    ("PUT", "/api/standesdb/contacts/", "Kontakt bearbeitet"),
    ("DELETE", "/api/standesdb/contacts/", "Kontakt gelöscht"),
    ("DELETE", "/api/archive/dirs/", "Ordner gelöscht"),
    ("PUT", "/api/archive/dirs/", "Ordner bearbeitet"),
    ("PUT", "/api/archive/files/", "Datei bearbeitet"),
    ("DELETE", "/api/archive/files/", "Datei gelöscht"),
    ("PUT", "/api/p4x/admin/accounts/", "Konto bearbeitet"),
    ("DELETE", "/api/p4x/admin/accounts/", "Konto gelöscht"),
    ("PUT", "/api/p4x/admin/categories/", "Kategorie bearbeitet"),
    ("DELETE", "/api/p4x/admin/categories/", "Kategorie gelöscht"),
    ("PUT", "/api/p4x/admin/transactions/", "Transaktion bearbeitet"),
    ("PUT", "/api/p4x/admin/category-filters/", "Filter bearbeitet"),
    ("DELETE", "/api/p4x/admin/category-filters/", "Filter gelöscht"),
    ("DELETE", "/api/p4x/admin/fee-config/", "Beitragskonfiguration gelöscht"),
    ("POST", "/api/p4x/admin/fee-members/", "Beitragsdaten bearbeitet"),
    ("PATCH", "/api/members/me/", "Profil bearbeitet"),
]


FAILED_LOGIN_PATHS = {"/api/auth/login", "/api/auth/google"}


def resolve_action_label(
    method: str,
    path: str,
    response_status: int = 200,
) -> str:
    if response_status == 401 and path in FAILED_LOGIN_PATHS:
        return "Anmeldung fehlgeschlagen"
    key = (method.upper(), path)
    if key in ACTION_LABELS:
        return ACTION_LABELS[key]
    for pat_method, segment, label in SUBRESOURCE_PATTERNS:
        if method.upper() == pat_method and segment in path:
            return label
    for pat_method, pat_prefix, label in ACTION_PATTERNS:
        if method.upper() == pat_method and path.startswith(pat_prefix):
            return label
    return f"{method.upper()} {path}"
