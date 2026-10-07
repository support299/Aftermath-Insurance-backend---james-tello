from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.authentication.models import Profile
from apps.teams.academy_sync import schedule_academy_push
from apps.teams.models import Team

_WATCHED = {'name', 'display_name', 'email', 'phone', 'team', 'team_id'}


def _membership_changed(update_fields):
    if update_fields is None:
        return True
    return bool(_WATCHED.intersection(update_fields))


@receiver(post_save, sender=Team)
def team_saved(sender, update_fields=None, raw=False, **kwargs):
    if raw or not _membership_changed(update_fields):
        return
    schedule_academy_push()


@receiver(post_delete, sender=Team)
def team_deleted(sender, **kwargs):
    schedule_academy_push()


@receiver(post_save, sender=Profile)
def profile_saved(sender, update_fields=None, raw=False, **kwargs):
    if raw or not _membership_changed(update_fields):
        return
    schedule_academy_push()


@receiver(post_delete, sender=Profile)
def profile_deleted(sender, **kwargs):
    schedule_academy_push()
