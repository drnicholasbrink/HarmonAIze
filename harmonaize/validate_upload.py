import io
from unittest.mock import MagicMock
from core.services.ai_harmonization_service import AIHarmonizationService
from core.services.attribute_description_rag_service import AttributeDescriptionRAGService

class StubClient:
    def __init__(self):
        self.recorded_types = []
        self.files = MagicMock()
        self.files.create.side_effect = self.mock_create

    def mock_create(self, file, purpose):
        self.recorded_types.append(type(file))
        return MagicMock(id='file-test')

stub = StubClient()

rag_service = object.__new__(AttributeDescriptionRAGService)
rag_service.client = stub

ai_service = object.__new__(AIHarmonizationService)
ai_service.client = stub

class FileLike(io.BytesIO):
    def __init__(self, content, path, name):
        super().__init__(content)
        self.path = path
        self.name = name

with open('requirements/base.txt', 'rb') as f:
    content = f.read()
    test_file = FileLike(content, 'requirements/base.txt', 'base.txt')

res1 = rag_service._upload_file(test_file)
test_file.seek(0)
res2 = ai_service._upload_file(test_file)

all_pass = (
    res1 == 'file-test' and 
    res2 == 'file-test' and 
    len(stub.recorded_types) == 2 and 
    all(issubclass(t, io.IOBase) or 'io' in str(t) for t in stub.recorded_types)
)

if all_pass:
    print('PASS')
else:
    print(f'FAIL: res1={res1}, res2={res2}, types={stub.recorded_types}')
