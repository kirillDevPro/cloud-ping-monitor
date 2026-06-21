"""Models for working with provider billing."""

from enum import Enum


class BillingModel(str, Enum):
    """
    Billing model of a cloud provider.

    PREPAID:
    - The user tops up the balance in advance
    - balance = remaining funds on the account (positive number)
    - pending_charges = upcoming charges
    - effective_balance = balance - pending_charges
    - Examples: Vultr, Hetzner, DigitalOcean

    POSTPAID:
    - Billed at the end of the month based on actual usage
    - balance = 0.0 (always)
    - monthly_costs = costs for the current month (MTD)
    - Examples: AWS, Google Cloud, Azure
    """

    PREPAID = "prepaid"
    POSTPAID = "postpaid"
