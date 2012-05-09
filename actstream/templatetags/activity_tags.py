from django.template import Variable, Library, Node, TemplateSyntaxError
from django.template.loader import render_to_string
from django.contrib.contenttypes.models import ContentType
from django.core.urlresolvers import reverse

from actstream.models import Follow

register = Library()


def _activity_templates(verb, template_name='action.html'):
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


class DisplayActivityFollowUrl(Node):
    def __init__(self, actor):
        self.actor = Variable(actor)

    def render(self, context):
        actor_instance = self.actor.resolve(context)
        content_type = ContentType.objects.get_for_model(actor_instance).pk
        if Follow.objects.is_following(context.get('user'), actor_instance):
            return reverse('actstream_unfollow', kwargs={
                'content_type_id': content_type, 'object_id': actor_instance.pk})
        return reverse('actstream_follow', kwargs={
            'content_type_id': content_type, 'object_id': actor_instance.pk})


class DisplayActivityActorUrl(Node):
    def __init__(self, actor):
        self.actor = Variable(actor)

    def render(self, context):
        actor_instance = self.actor.resolve(context)
        content_type = ContentType.objects.get_for_model(actor_instance).pk
        return reverse('actstream_actor', kwargs={
            'content_type_id': content_type, 'object_id': actor_instance.pk})


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
        bits = token.split_contents()
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


class DisplayAction(AsNode):

    def render_result(self, context):
        action_instance = self.args[0].resolve(context)
        templates = _activity_templates(action_instance.verb)
        return render_to_string(templates, {'action': action_instance},
            context)


def display_action(parser, token):
    """
    Renders the template for the action description

    Example::

        {% display_action action %}
    """
    return DisplayAction.handle_token(parser, token)


def is_following(user, actor):
    """
    Returns true if the given user is following the actor

    Example::

        {% if user|is_following:another_user %}
            You are already following {{ another_user }}
        {% endif %}
    """
    return Follow.objects.is_following(user, actor)


def follow_url(parser, token):
    """
    Renders the URL of the follow view for a particular actor instance

    Example::

        <a href="{% follow_url other_user %}">
            {% if user|is_following:other_user %}
                stop following
            {% else %}
                follow
            {% endif %}
        </a>

    """
    bits = token.split_contents()
    if len(bits) != 2:
        raise TemplateSyntaxError("Accepted format "
            "{% follow_url [instance] %}")
    return DisplayActivityFollowUrl(bits[1])


def actor_url(parser, token):
    """
    Renders the URL for a particular actor instance

    Example::

        <a href="{% actor_url user %}">View your actions</a>
        <a href="{% actor_url another_user %}">{{ another_user }}'s actions</a>

    """
    bits = token.split_contents()
    if len(bits) != 2:
        raise TemplateSyntaxError("Accepted format "
            "{% actor_url [actor_instance] %}")
    return DisplayActivityActorUrl(*bits[1:])


class DisplayGroupedActions(AsNode):
    args_count = 2

    def render_result(self, context):
        groups = group_verbs(actions=self.args[0].resolve(context),
            aggressiveness=self.args[1].resolve(context))
        output = []
        for verb, actions in groups:
            templates = _activity_templates(verb, template_name='actions.html')
            output += render_to_string(templates,
                {'verb': verb, 'actions': actions}, context)
        return ''.join(output)


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


register.filter(is_following)
register.tag(display_action)
register.tag(follow_url)
register.tag(actor_url)
register.tag(display_grouped_actions)
