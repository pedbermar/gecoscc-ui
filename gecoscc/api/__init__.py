from bson import ObjectId

from cornice.schemas import CorniceSchema


SAFE_METHODS = ('GET', 'OPTIONS', 'HEAD')
UNSAFE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')


class ResourcePaginatedReadOnly(object):
    # TODO
    # Implement permissions filter

    schema_collection = None
    schema_detail = None
    mongo_filter = {
        'type': 'anytype',
    }
    collection_name = 'nodes'

    def __init__(self, request):
        self.request = request
        self.default_pagesize = request.registry.settings.get(
            'default_pagesize', 30)

    def parse_item(self, item):
        serialized_item = self.schema_detail().deserialize(item)
        return self.schema_detail().serialize(serialized_item)

    def parse_collection(self, collection):
        serialized_data = self.schema_collection().deserialize(collection)
        return self.schema_collection().serialize(serialized_data)

    def get_objects_filter(self):
        return {}

    def get_object_filter(self):
        return {}

    def get_collection(self):
        return self.request.db[self.collection_name]

    def collection_get(self):
        page = int(self.request.GET.get('page', 0))
        pagesize = int(self.request.GET.get('pagesize', self.default_pagesize))

        extraargs = {}
        if pagesize > 0:
            extraargs.update({
                'skip': page*pagesize,
                'limit': pagesize,
            })

        users_count = self.request.db.nodes.find(
            {'type': 'user'},
            {'type': 1}
        ).count()

        collection_filter = self.get_objects_filter()
        collection_filter.update(self.mongo_filter)

        collection = self.get_collection()
        objects = collection.find(collection_filter, **extraargs)
        if pagesize > 0:
            pages = int(users_count / pagesize)
        else:
            pagesize = 1
        return {
            'pagesize': pagesize,
            'pages': pages,
            'page': page,
            self.collection_name: self.parse_collection(list(objects)),
        }

    def get(self):
        oid = self.request.matchdict['oid']
        collection = self.get_collection()

        collection_filter = {
            '_id': ObjectId(oid),
        }
        collection_filter.update(self.get_object_filter())
        collection_filter.update(self.mongo_filter)
        user = collection.find_one(collection_filter)
        return self.parse_item(user)


class ResourcePaginated(ResourcePaginatedReadOnly):

    def __init__(self, request):
        super(ResourcePaginated, self).__init__(request)
        if request.method in UNSAFE_METHODS:
            self.schema = CorniceSchema(self.schema_detail)

    def collection_post(self):
        return {"test": "not implemented"}

    def put(self):
        return {"test": "not implemented"}

    def delete(self):
        return {"test": "not implemented"}
