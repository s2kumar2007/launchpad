from abc import ABC, abstractmethod

class BaseStore(ABC):
    @abstractmethod
    def availability(self, sid: str, date: str, at: str = "10:00", staff_id: str | None = None) -> list:
        """Top 3 free slots near requested time."""
        pass

    @abstractmethod
    def book(self, cust: str, sid: str, date: str, at: str, staff_id: str | None = None) -> dict:
        """Book a slot idempotently."""
        pass

    @abstractmethod
    def reschedule(self, cust: str, bid: str, date: str, at: str, staff_id: str | None = None) -> dict:
        """Atomically reschedule an active booking to a new slot."""
        pass

    @abstractmethod
    def cancel(self, cust: str, bid: str) -> dict:
        """Cancel a booking, offering freed slot to waitlist if available."""
        pass

    @abstractmethod
    def join_waitlist(self, cust: str, sid: str, reliability: float = 0.8) -> dict:
        """Join waitlist for a service."""
        pass

    @abstractmethod
    def accept_offer(self, cust: str, oid: str) -> dict:
        """Accept an offered slot."""
        pass

    @abstractmethod
    def mine(self, cust: str) -> dict:
        """Customer's active bookings, offers, and waitlisted services."""
        pass

    @abstractmethod
    def stats(self) -> dict:
        """Business statistics for bookings, cancellations, and refills."""
        pass
