import mixedbread as mxbai
import os
from pathlib import Path
import tempfile
import json

from shellbot2.tools.util import classproperty


def format_data_chunk(chunk) -> str:
    return json.dumps(chunk.to_json(), indent=2)

class DocStoreTool:
    def __init__(self, store_id: str = None):
        self.mixedbread = mxbai.Mixedbread(api_key=os.getenv("MIXEDBREAD_API_KEY"))
        if store_id is None:
            store_id = os.getenv("MIXEDBREAD_STORE_ID")
            if store_id is None:
                raise ValueError("MIXEDBREAD_STORE_ID is not set")
        self.store_id = store_id
        assert self.store_id, "store-id is required"
        # To get a path to a suitable directory for temporary files (like /tmp), use tempfile.gettempdir()
        self.download_dir = os.getenv("SHELLBOT_DOWNLOAD_DIR", "~/Downloads")

    @property
    def name(self):
        return "document-store"
    
    @classproperty
    def toolname(cls):
        return "document-store"
    
    @property
    def description(self):
        return """This function manages a document store for storing and retrieving documents. 
        It can store most kinds of files including text, PDF, images, and other types. It supports uploading files, downloading files, and semantic searching of file contents, even for images.
        The 'search' operation searches documents and returns a list of high-scoring document chunks, the results include file-id that can be used to download the file if needed.
        The 'upload' operation uploads a file to the document store. It may not be ready immediately for searching.
        The 'download' operation downloads a file from the document store. If the destination_dir is not provided, the file is downloaded to the system temp directory. Either way, the path to the downloaded file is returned.
        The 'list' operation lists all files in the document store along with their file-ids, and parsing status (pending, completed, failed, etc).
        """
    
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "The operation to perform: 'search' to search documents, 'download' to download a file, 'upload' to upload a file, or 'list' to list all files in the document store",
                    "enum": ["search", "download", "upload", "list"]
                },
                "query": {
                    "type": "string",
                    "description": "Search query for document contents, may be a few short keywords or simple phrase. Required when operation is 'search'."
                },
                "file_id": {
                    "type": "string",
                    "description": "The ID of the file to download. Required when operation is 'download'. "
                },
                "destination_dir": {
                    "type": "string",
                    "description": "The destination directory for downloaded files. Optional, defaults to system temp directory. Required when operation is 'download'."
                },
                "file_path": {
                    "type": "string",
                    "description": "The local file path to upload. Required when operation is 'upload'."
                }
            },
            "required": ["operation"]
        }

    def __call__(self, **kwargs):
        op = kwargs.get('operation')
        if not op:
            return f"The function {self.name} requires an 'operation' keyword argument, but didn't get one"
        if op == "search":
            query = kwargs.get('query')
            if not query:
                return f"The function {self.name} with operation {op} requires a 'query' keyword argument, but didn't get one"
            results = self.mixedbread.stores.search(query=query, store_identifiers=[self.store_id], top_k=5)
            return "\n==========\n".join([format_data_chunk(chunk) for chunk in results.data])
        elif op == "download":
            file_id = kwargs.get('file_id')
            response = self.mixedbread.files.content(file_id)
            filename = kwargs.get('filename')
            destination_dir = kwargs.get('destination_dir', tempfile.gettempdir())
            if not os.path.exists(destination_dir):
                return f"Destination directory {destination_dir} does not exist"
            destination_path = os.path.join(destination_dir, filename)
            with open(destination_path, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
            return f"File {filename} downloaded to {destination_path}"
        elif op == "upload":
            file_path = kwargs.get('file_path')
            if not os.path.exists(file_path):
                return f"File {file_path} does not exist"
            filename = os.path.basename(file_path)

            # First, create the file in mixedbread
            res = self.mixedbread.stores.files.upload(
                store_identifier=self.store_id,
                file=Path(file_path),
            )
            return f"Uploaded file {filename}, response: {res}"
        elif op == "list":
            results = self.mixedbread.stores.files.list(store_identifier=self.store_id, limit=100)
            return json.dumps(results.to_json(), indent=2)

if __name__ == "__main__":
    docstoretool = DocStoreTool()
    print(docstoretool(operation="upload", file_path="/Users/brendanofallon/Downloads/jcm-14-05110.pdf"))