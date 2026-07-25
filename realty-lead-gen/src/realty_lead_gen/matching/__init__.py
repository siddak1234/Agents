"""Buyer <-> property matching.

Buyer-side: given a new property, find interested buyers.
Seller-side: given a buyer profile / market signal, find interested owners.
"""

from realty_lead_gen.matching.buyer_intent import BuyerMatcher

__all__ = ["BuyerMatcher"]
