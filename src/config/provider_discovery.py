"""Auto-discovery of providers from environment variables.

This module automatically discovers providers by matching environment
variable name patterns:
- HETZNER_{SUFFIX}_API_KEY -> type hetzner, alias hetzner_{suffix}
- VULTR_{SUFFIX}_API_KEY -> type vultr, alias vultr_{suffix}
- AWS_{SUFFIX}_ACCESS_KEY_ID + AWS_{SUFFIX}_SECRET_ACCESS_KEY -> type aws, alias aws_{suffix}

Examples:
- HETZNER_PROD_API_KEY -> alias=hetzner_prod, display_name="Hetzner (prod)"
- VULTR_MAIN_API_KEY -> alias=vultr_main, display_name="Vultr" (the "main" suffix is hidden)
- AWS_PROD_ACCESS_KEY_ID -> alias=aws_prod, display_name="AWS (prod)"
"""

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from ..models.provider import ProviderConfig, ProviderType

logger = logging.getLogger(__name__)

# The .env file Settings loads provider keys from (project root). Discovery reads
# the same source so it stays consistent no matter which entry point built it.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _env_source() -> dict[str, str]:
    """Return env vars from the .env file overlaid by os.environ.

    Settings reads provider keys from .env via Pydantic's dotenv source, which
    does NOT populate os.environ. Reading the same merged view here means a
    maintenance script, test, or any entry point that constructs Settings
    discovers the same providers as main.py. os.environ wins on conflicts.

    Returns:
        dict[str, str]: Merged environment, .env values overridden by os.environ.
    """
    merged: dict[str, str] = {
        key: value for key, value in dotenv_values(_ENV_FILE).items() if value is not None
    }
    merged.update(os.environ)
    return merged

# Patterns for parsing environment variable names
PROVIDER_PATTERNS: dict[ProviderType, re.Pattern[str]] = {
    ProviderType.HETZNER: re.compile(r"^HETZNER_([A-Z0-9_]+)_API_KEY$"),
    ProviderType.VULTR: re.compile(r"^VULTR_([A-Z0-9_]+)_API_KEY$"),
    ProviderType.AWS: re.compile(r"^AWS_([A-Z0-9_]+)_ACCESS_KEY_ID$"),
}

# AWS secret-key pattern (the partner of the AWS access-key pattern above).
# Kept module-level so it is compiled once, not per environment variable.
AWS_SECRET_PATTERN: re.Pattern[str] = re.compile(r"^AWS_([A-Z0-9_]+)_SECRET_ACCESS_KEY$")

# Telegram caps callback_data at 64 bytes. The longest prefix that embeds an alias
# raw (balance/provider keyboards, e.g. "balance_history_30:") is ~19 bytes; with a
# safety margin an alias beyond ~40 bytes risks BUTTON_DATA_INVALID on those buttons
# (40 + 19 = 59 < 64). Warn only.
MAX_SAFE_ALIAS_LEN = 40

# Default emoji per provider type
DEFAULT_EMOJI: dict[ProviderType, str] = {
    ProviderType.HETZNER: "[H]",
    ProviderType.VULTR: "[V]",
    ProviderType.AWS: "[A]",
}

# Default display name per provider type
DEFAULT_DISPLAY_NAME: dict[ProviderType, str] = {
    ProviderType.HETZNER: "Hetzner",
    ProviderType.VULTR: "Vultr",
    ProviderType.AWS: "AWS",
}


@dataclass
class DiscoveredProvider:
    """A provider discovered from environment variables.

    Attributes:
        alias: Unique identifier (e.g. hetzner_prod).
        type: Provider type (hetzner, vultr, aws).
        suffix: Suffix extracted from the variable name (main, prod, staging).
        api_key: API key for Hetzner/Vultr (optional).
        access_key_id: AWS Access Key ID (optional).
        secret_access_key: AWS Secret Access Key (optional).
    """

    alias: str
    type: ProviderType
    suffix: str
    api_key: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None


def discover_providers_from_env() -> dict[str, DiscoveredProvider]:
    """Scan environment variables and find providers by matching name patterns.

    Returns:
        dict[str, DiscoveredProvider]: Mapping of {alias: DiscoveredProvider}.

    Environment variable examples:
        - HETZNER_PROD_API_KEY=xxx -> alias=hetzner_prod
        - VULTR_MAIN_API_KEY=xxx -> alias=vultr_main
        - AWS_PROD_ACCESS_KEY_ID=xxx + AWS_PROD_SECRET_ACCESS_KEY=xxx -> alias=aws_prod
    """
    discovered: dict[str, DiscoveredProvider] = {}

    # Tracks AWS keys per suffix (both keys are required)
    aws_candidates: dict[str, dict[str, str]] = {}

    for env_var, value in _env_source().items():
        # Skip empty values
        if not value or not value.strip():
            continue

        # Check Hetzner and Vultr patterns
        for provider_type in [ProviderType.HETZNER, ProviderType.VULTR]:
            pattern = PROVIDER_PATTERNS[provider_type]
            match = pattern.match(env_var)
            if match:
                suffix = match.group(1).lower()
                alias = f"{provider_type.value}_{suffix}"

                discovered[alias] = DiscoveredProvider(
                    alias=alias,
                    type=provider_type,
                    suffix=suffix,
                    api_key=value.strip(),
                )
                logger.debug("Discovered provider: %s from %s", alias, env_var)
                break

        # Check the AWS access-key pattern
        aws_pattern = PROVIDER_PATTERNS[ProviderType.AWS]
        match = aws_pattern.match(env_var)
        if match:
            suffix = match.group(1).lower()
            if suffix not in aws_candidates:
                aws_candidates[suffix] = {}
            aws_candidates[suffix]["access_key_id"] = value.strip()

        # Check the AWS secret-key pattern
        match = AWS_SECRET_PATTERN.match(env_var)
        if match:
            suffix = match.group(1).lower()
            if suffix not in aws_candidates:
                aws_candidates[suffix] = {}
            aws_candidates[suffix]["secret_access_key"] = value.strip()

    # Build AWS providers (both keys are required)
    for suffix, keys in aws_candidates.items():
        access_key = keys.get("access_key_id")
        secret_key = keys.get("secret_access_key")

        if access_key and secret_key:
            alias = f"aws_{suffix}"
            discovered[alias] = DiscoveredProvider(
                alias=alias,
                type=ProviderType.AWS,
                suffix=suffix,
                access_key_id=access_key,
                secret_access_key=secret_key,
            )
            logger.debug("Discovered AWS provider: %s", alias)
        elif access_key or secret_key:
            # Only one key present - log a warning
            logger.warning(
                "AWS provider '%s' has incomplete credentials (need both ACCESS_KEY_ID and SECRET_ACCESS_KEY)",
                suffix,
            )

    # Warn about aliases long enough to overflow Telegram's 64-byte callback_data
    # on the raw balance/provider buttons (BUTTON_DATA_INVALID).
    for alias in discovered:
        if len(alias) > MAX_SAFE_ALIAS_LEN:
            logger.warning(
                "Provider alias '%s' (%d chars) may overflow Telegram's 64-byte "
                "callback_data limit on balance/provider buttons; use a shorter suffix",
                alias,
                len(alias),
            )

    return discovered


def generate_provider_config(discovered: DiscoveredProvider) -> ProviderConfig:
    """Generate a ProviderConfig from a DiscoveredProvider using default values.

    Args:
        discovered: The discovered provider.

    Returns:
        ProviderConfig: Provider configuration with auto-generated values.

    display_name generation rules:
        - If suffix="main" -> only the type ("Vultr", "Hetzner", "AWS").
        - Otherwise -> "{Type} ({suffix})" ("Hetzner (prod)", "AWS (prod)").
    """
    base_name = DEFAULT_DISPLAY_NAME.get(discovered.type, discovered.type.value.title())
    emoji = DEFAULT_EMOJI.get(discovered.type, "[?]")

    # Generate display_name
    if discovered.suffix.lower() == "main":
        display_name = base_name
    else:
        display_name = f"{base_name} ({discovered.suffix})"

    # Build the configuration
    config = ProviderConfig(
        alias=discovered.alias,
        type=discovered.type,
        display_name=display_name,
        emoji=emoji,
    )

    # AWS-specific defaults (all regions, EC2 + Lightsail)
    if discovered.type == ProviderType.AWS:
        config.regions = None  # all regions
        config.enable_ec2 = True
        config.enable_lightsail = True

    return config


def get_provider_api_key_from_env(alias: str) -> str | None:
    """Get a provider's API key directly from environment variables.

    Args:
        alias: Provider alias (e.g. "hetzner_prod").

    Returns:
        str | None: The API key, or None if not found.
    """
    # Format: {ALIAS}_API_KEY (the alias already includes the type)
    env_var = f"{alias.upper()}_API_KEY"
    return _env_source().get(env_var)


def get_provider_aws_credentials_from_env(alias: str) -> tuple[str, str] | None:
    """Get AWS credentials directly from environment variables.

    Args:
        alias: Provider alias (e.g. "aws_prod").

    Returns:
        tuple[str, str] | None: (access_key_id, secret_access_key), or None
        if either key is missing.
    """
    alias_upper = alias.upper()
    env = _env_source()
    access_key = env.get(f"{alias_upper}_ACCESS_KEY_ID")
    secret_key = env.get(f"{alias_upper}_SECRET_ACCESS_KEY")

    if access_key and secret_key:
        return (access_key, secret_key)

    return None
