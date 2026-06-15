import uuid

from django.db import models


class Carrier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    carrier_type = models.TextField(default="health")
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "carriers"
        indexes = [models.Index(fields=["active"])]

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField()
    carrier = models.ForeignKey(
        Carrier,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        db_column="carrier_id",
        related_name="products",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "products"
        constraints = [
            models.UniqueConstraint(
                fields=["carrier", "name"], name="products_carrier_name_unique"
            ),
        ]
        indexes = [models.Index(fields=["active"]), models.Index(fields=["carrier"])]

    def __str__(self) -> str:
        return self.name


class AddOn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "add_ons"
        indexes = [models.Index(fields=["active"])]

    def __str__(self) -> str:
        return self.name


class LeadSource(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.TextField(unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "lead_sources"
        indexes = [models.Index(fields=["active"])]

    def __str__(self) -> str:
        return self.name
