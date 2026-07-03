"""Base collector class — all AWS collectors inherit from this."""

from __future__ import annotations
from abc import ABC, abstractmethod
import boto3
from ...models import Resource


class BaseCollector(ABC):
    """Abstract base for all AWS resource collectors."""

    resource_type: str = ""  # e.g., "aws.ec2"

    def __init__(self, session: boto3.Session, region: str, account_id: str):
        self.session = session
        self.region = region
        self.account_id = account_id

    @abstractmethod
    def collect(self) -> list[Resource]:
        """Collect resources from AWS. Must be implemented by subclasses."""
        ...

    def _make_resource(self, resource_id: str, tags: dict = None, properties: dict = None, metrics: dict = None) -> Resource:
        """Helper to create a Resource with common fields pre-filled."""
        return Resource(
            resource_id=resource_id,
            resource_type=self.resource_type,
            region=self.region,
            account_id=self.account_id,
            tags=tags or {},
            properties=properties or {},
            metrics=metrics or {},
        )
