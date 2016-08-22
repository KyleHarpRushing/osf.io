from modularodm.exceptions import ValidationValueError
from rest_framework import exceptions
from rest_framework import serializers as ser

from api.base.serializers import (
    JSONAPISerializer, IDField, JSONAPIListField, LinksField, RelationshipField
)
from api.base.exceptions import Conflict
from api.base.utils import absolute_reverse, get_user_auth
from api.nodes.serializers import NodeTagField
from api.taxonomies.serializers import TaxonomyField
from framework.exceptions import PermissionsError
from website.models import Node, StoredFileNode


class PrimaryFileRelationshipField(RelationshipField):
    def get_object(self, file_id):
        return StoredFileNode.load(file_id)

    def to_internal_value(self, data):
        file = self.get_object(data)
        return {'primary_file': file}


class PreprintSerializer(JSONAPISerializer):

    filterable_fields = frozenset([
        'id',
        'title',
        'tags',
        'date_created',
        'date_modified',
        'contributors',
        'provider',
        'subjects',
        'doi'
    ])

    title = ser.CharField(required=False)
    subjects = JSONAPIListField(child=TaxonomyField(), required=False, source='preprint_subjects')
    provider = ser.CharField(source='preprint_provider', required=False)
    date_created = ser.DateTimeField(read_only=True, source='preprint_created')
    date_modified = ser.DateTimeField(read_only=True)
    id = IDField(source='_id', required=False)
    abstract = ser.CharField(source='description', required=False)
    tags = JSONAPIListField(child=NodeTagField(), required=False)
    doi = ser.CharField(source='preprint_doi', required=False)

    primary_file = PrimaryFileRelationshipField(
        related_view='files:file-detail',
        related_view_kwargs={'file_id': '<preprint_file._id>'},
        lookup_url_kwarg='file_id',
        read_only=False
    )

    files = RelationshipField(
        related_view='nodes:node-providers',
        related_view_kwargs={'node_id': '<pk>'}
    )

    links = LinksField(
        {
            'self': 'get_preprint_url',
            'html': 'get_absolute_html_url',
            'doi': 'get_doi_url'
        }
    )

    contributors = RelationshipField(
        related_view='preprints:preprint-contributors',
        related_view_kwargs={'node_id': '<pk>'},
        related_meta={'count': 'get_contrib_count'},
    )

    class Meta:
        type_ = 'preprints'

    def get_preprint_url(self, obj):
        return absolute_reverse('preprints:preprint-detail', kwargs={'node_id': obj._id})

    def get_absolute_url(self, obj):
        return self.get_preprint_url(obj)

    def get_doi_url(self, obj):
        return 'https://dx.doi.org/{}'.format(obj.preprint_doi)

    def create(self, validated_data):
        node = Node.load(validated_data.pop('_id', None))

        if not node:
            raise exceptions.NotFound('Unable to find Node with specified id.')
        if node.is_preprint:
            raise Conflict('This node already stored as a preprint, use the update method instead.')

        auth = get_user_auth(self.context['request'])
        primary_file = validated_data.pop('primary_file', None)
        if not primary_file:
            raise exceptions.ValidationError(detail='You must specify a primary_file to create a preprint.')

        self.set_node_field(node.set_preprint_file, primary_file, auth)

        subjects = validated_data.pop('preprint_subjects', None)
        if not subjects:
            raise exceptions.ValidationError(detail='You must specify at least one subject to create a preprint.')

        self.set_node_field(node.set_preprint_subjects, subjects, auth)

        for key, value in validated_data.iteritems():
            setattr(node, key, value)
        try:
            node.save()
        except ValidationValueError as e:
            raise exceptions.ValidationError(detail=e.message)
        return node

    def update(self, node, validated_data):
        from website.models import Node
        assert isinstance(node, Node), 'You must specify a valid node to be updated.'
        auth = get_user_auth(self.context['request'])
        primary_file = validated_data.pop('primary_file', None)
        if primary_file:
            self.set_node_field(node.set_preprint_file, primary_file, auth)
        subjects = validated_data.pop('preprint_subjects', None)
        if subjects:
            self.set_node_field(node.set_preprint_subjects, subjects, auth)

        for key, value in validated_data.iteritems():
            setattr(node, key, value)
        try:
            node.save()
        except ValidationValueError as e:
            raise exceptions.ValidationError(detail=e.message)
        return node

    def set_node_field(self, func, val, auth):
        try:
            func(val, auth, save=False)
        except PermissionsError:
            raise exceptions.PermissionDenied('Not authorized to update this node.')
        except ValueError as e:
            raise exceptions.ValidationError(detail=e.message)