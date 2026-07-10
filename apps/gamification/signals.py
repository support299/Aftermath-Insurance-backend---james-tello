from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.sales.models import Sale


@receiver(post_save, sender=Sale)
def on_sale_saved(sender, instance, **kwargs):
    from apps.gamification.services import handle_sale_change

    handle_sale_change(instance)


@receiver(post_delete, sender=Sale)
def on_sale_deleted(sender, instance, **kwargs):
    from apps.gamification.services import handle_sale_change

    handle_sale_change(instance, is_delete=True)
