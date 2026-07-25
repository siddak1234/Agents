"""SQLAlchemy ORM models.

Import every model here so Alembic's autogenerate picks them up.
"""

from realty_lead_gen.models.audit import AuditEvent
from realty_lead_gen.models.base import Base, TimestampMixin, UUIDPKMixin
from realty_lead_gen.models.buyer import BuyerProfile, SavedSearch
from realty_lead_gen.models.contact import ContactChannel
from realty_lead_gen.models.deal import DealAnalysis
from realty_lead_gen.models.enrichment import EnrichmentRun
from realty_lead_gen.models.lead import Lead, LeadFeedback
from realty_lead_gen.models.listing import Listing
from realty_lead_gen.models.outbox import OutboxEvent
from realty_lead_gen.models.owner import Owner, PropertyOwnership
from realty_lead_gen.models.photo import Photo, PhotoAnalysis
from realty_lead_gen.models.property import Property, PropertySnapshot
from realty_lead_gen.models.score import Score
from realty_lead_gen.models.signal import Signal
from realty_lead_gen.models.user import User

__all__ = [
    "AuditEvent",
    "Base",
    "BuyerProfile",
    "ContactChannel",
    "DealAnalysis",
    "EnrichmentRun",
    "Lead",
    "LeadFeedback",
    "Listing",
    "OutboxEvent",
    "Owner",
    "Photo",
    "PhotoAnalysis",
    "Property",
    "PropertyOwnership",
    "PropertySnapshot",
    "SavedSearch",
    "Score",
    "Signal",
    "TimestampMixin",
    "UUIDPKMixin",
    "User",
]
