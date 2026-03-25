# accounts/permissions.py


from rest_framework import permissions
from .models import SavingsGoal, SavingsGroup, GroupJoinRequest

class IsGroupAdmin(permissions.BasePermission):
    """
    Custom permission to only allow the admin of a group to perform certain actions.
    This works for views where the group_id is in the URL ( /groups/{id}/requests/).
    """
    message = 'You must be the administrator of this group to perform this action.'

    def has_permission(self, request, view):
        # Allow if the user is authenticated
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        if isinstance(obj, SavingsGroup):
            # Check if the requesting user is the admin of the group
            return obj.admin == request.user

        if isinstance(obj, GroupJoinRequest):
            return obj.group.admin == request.user

        return False

class IsGoalOwner(permissions.BasePermission):
    message = "You can only manage goals you own."

    def has_object_permission(self, request, view, obj):
        if isinstance(obj, SavingsGoal):
            return obj.user == request.user
        return False
