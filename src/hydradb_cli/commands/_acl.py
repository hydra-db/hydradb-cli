"""Shared ACL contract wording for command help."""

ACL_WARNING_CODE = "acl_ignored_by_connector_enforcement"
ACL_HELP = (
    "Optional principals for non-enforced connectors (repeatable). "
    "Enforced connectors use the authenticated caller and ignore supplied ACL. "
    "Only eligible human sessions can change connector enforcement."
)
ACL_WARNING_EPILOG = f"Enforcement warning code: {ACL_WARNING_CODE}"
