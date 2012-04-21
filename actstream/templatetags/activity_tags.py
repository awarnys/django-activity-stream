from django.template import Variable, Library, Node, TemplateSyntaxError,\
    VariableDoesNotExist
from django.template.loader import render_to_string
from django.contrib.contenttypes.models import ContentType
from django.core.urlresolvers import reverse

from actstream.models import Follow

register = Library()


# Helpers

class AsNode(Node):
    """
    Base template Node class for template tags that takes a predefined number
    of arguments, ending in an optional 'as var' section.
    """
    args_count = 1

    @classmethod
    def handle_token(cls, parser, token):
        """
        Class method to parse and return a Node.
        """
        bits = token.contents.split()
        args_count = len(bits) - 1
        if args_count >= 2 and bits[-2] == 'as':
            as_var = bits[-1]
            args_count -= 2
        else:
            as_var = None
        if args_count != cls.args_count:
            arg_list = ' '.join(['[arg]' * cls.args_count])
            raise TemplateSyntaxError("Accepted formats {%% %(tagname)s "
                "%(args)s %%} or {%% %(tagname)s %(args)s as [var] %%}" %
                {'tagname': bits[0], 'args': arg_list})
        args = [parser.compile_filter(token) for token in
            bits[1:args_count + 1]]
        return cls(args, varname=as_var)

    def __init__(self, args, varname=None):
        self.args = args
        self.varname = varname

    def render(self, context):
        result = self.render_result(context)
        if self.varname is not None:
            context[self.varname] = result
            return ''
        return result

    def render_result(self, context):
        raise NotImplementedError("Must be implemented by a subclass")


def activity_templates(verb, template_name='action.html'):
    return [
        'activity/%s/%s' % (verb.replace(' ', '_'), template_name),
        'activity/%s' % template_name,
    ]


def group_verbs(actions, aggressiveness=0):
    verbs = []
    groups = {}
    for action in actions:
        if action.verb not in verbs:
            if len(verbs) > aggressiveness:
                verb = verbs.pop(0)
                yield (verb, groups.pop(verb))
            verbs.append(action.verb)
        groups.setdefault(action.verb, []).append(action)
    for verb in verbs:
        yield (verb, groups[verb])


# Filters

@register.filter
def is_following(user, obj):
    return user.following_activities.for_object(obj).exists()


@register.filter
def activity_followers_count(instance):
    return Follow.objects.for_object(instance).count()


# Tags

class DisplayActivityFollowUrl(Node):
    def __init__(self, obj):
        self.obj = obj

    def render(self, context):
        user = context.get('user')
        obj = self.obj.resolve(context)
        content_type = ContentType.objects.get_for_model(obj).pk
        if user.following_activities.for_object(obj).exists():
            url_name = 'actstream_unfollow'
        else:
            url_name = 'actstream_follow'
        return reverse(url_name, kwargs={'content_type_id': content_type,
            'object_id': obj.pk})


@register.tag
def activity_follow_url(parser, tokens):
    bits = tokens.contents.split()
    if len(bits) != 3:
        raise TemplateSyntaxError("Accepted format "
            "{% activity_follow_url [instance] %}")
    return DisplayActivityFollowUrl(obj=parser.compile_filter(bits[1]))


@register.simple_tag
def activity_followers_url(instance):
    content_type = ContentType.objects.get_for_model(instance).pk
    return reverse('actstream_followers',
        kwargs={'content_type_id': content_type, 'object_id': instance.pk})


class DisplayActionLabel(AsNode):

    def render_result(self, context):
        actor_instance = self.args[0].resolve(context)
        try:
            user = Variable("request.user").resolve(context)
        except VariableDoesNotExist:
            user = None
        try:
            if user and user == actor_instance.user:
                result = " your "
            else:
                result = " %s's " % (actor_instance.user.get_full_name() or
                    actor_instance.user.username)
        except ValueError:
            result = ""
        result += actor_instance.get_label()
        return result


@register.tag
def action_label(parser, token):
    return DisplayActionLabel.handle_token(parser, token)


class DisplayAction(AsNode):

    def render_result(self, context):
        action = self.args[0].resolve(context)
        templates = activity_templates(action.verb)
        return render_to_string(templates, {'action': action}, context)


@register.tag
def display_action(parser, token):
    """
    Renders an action.

    Usage::

        {% display_action <action> %}

    Alternatively, the action can be rendered to a context variable rather than
    being rendered inline by using the format::

        {% display_action <action> as <variable_name> %}
    """
    return DisplayAction.handle_token(parser, token)


class DisplayActionShort(AsNode):

    def render_result(self, context):
        action = self.args[0].resolve(context)
        templates = activity_templates(action.verb)
        return render_to_string(templates,
            {'action': action, 'hide_actor': True}, context)


@register.tag
def display_action_short(parser, token):
    """
    Renders an action, hiding the actor from the action output.

    Usage::

        {% display_action_short <action> %}

    Alternatively, the action can be rendered to a context variable rather than
    being rendered inline by using the format::

        {% display_action_short <action> as <variable_name> %}
    """
    return DisplayActionShort.handle_token(parser, token)


class DisplayGroupedActions(AsNode):
    args_count = 2

    def render_result(self, context):
        groups = group_verbs(actions=self.args[0].resolve(context),
            aggressiveness=self.args[1].resolve(context))
        output = []
        for verb, actions in groups:
            templates = activity_templates(verb, template_name='actions.html')
            output += render_to_string(templates,
                {'verb': verb, 'actions': actions}, context)
        return ''.join(output)


@register.tag
def display_grouped_actions(parser, token):
    """
    Display actions, grouped by verbs.

    Usage: ``{% display_grouped_actions <actions> <aggressiveness> %}``

    ``actions`` should be an iterable of activity stream actions.

    ``aggressiveness`` should be an integer as to how aggressively to group
    actions. It correlates to the number of "gaps" allowed between different
    action types grouping.

    For example, here are several different aggressiveness ratings and how it
    would order the given list::

        [<post 1>, <update 1>, <update 2>, <remove 1>, <update 3>, <remove 2>, <share 1>, <update 4>, <share 2>]

        {% display_grouped_actions actions 0 %}

        [<post 1>]
        [<update 1>, <update 2>]
        [<remove 1>]
        [<update 3>]
        [<remove 2>]
        [<share 1>]
        [<update 4>]
        [<share 2>]

        {% display_grouped_actions actions 1 %}

        [<post 1>]
        [<update 1>, <update 2>, <update 3>]
        [<remove 1>, <remove 2>]
        [<share 1>, <share 2>]
        [<update 4>]

        {% display_grouped_actions actions 2 %}

        [<post 1>]
        [<update 1>, <update 2>, <update 3>, <update 4>]
        [<remove 1>, <remove 2>]
        [<share 1>, <share 2>]

    Each group of actions is rendered using ``activity/<verb>/actions.html``,
    falling back to ``activity/actions.html`` if no verb-specific template is
    provided.

    If required, the actions can be rendered to a context variable rather than
    being rendered inline by using the format::

        {% display_grouped_actions <actions> <aggressiveness> as <variable_name> %}
    """
    return DisplayGroupedActions.handle_token(parser, token)
