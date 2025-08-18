"""
Context processors for the core app.
"""
from .models import Study


def target_study_context(request):
    """
    Add target study information to all templates.
    """
    context = {}
    
    if request.user.is_authenticated:
        target_study = Study.objects.filter(
            created_by=request.user,
            study_purpose='target'
        ).first()
        
        context.update({
            'user_target_study': target_study,
            'has_target_study': target_study is not None,
        })
    
    return context
