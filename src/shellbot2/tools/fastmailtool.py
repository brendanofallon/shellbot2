import requests
import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from shellbot2.tools.util import classproperty

import requests
import json
from datetime import datetime, timezone, timedelta

import requests
import json
from datetime import datetime, timezone, timedelta

class FastmailClient:
    SESSION_URL = "https://api.fastmail.com/jmap/session"

    def __init__(self, token: str = None):
        if token is None:
            token = os.getenv("FASTMAIL_API_TOKEN")
        if not token:
            raise ValueError("Fastmail API token is required. Set FASTMAIL_API_TOKEN env var or pass token parameter.")
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        })
        self.api_url = None
        self.account_id = None
        self._refresh_session()

    def _refresh_session(self):
        try:
            resp = self.session.get(self.SESSION_URL)
            resp.raise_for_status()
            data = resp.json()
            self.api_url = data['apiUrl']
            self.account_id = data['primaryAccounts']['urn:ietf:params:jmap:mail']
        except Exception as e:
            raise RuntimeError(f"Session init failed: {e}")

    def _call_jmap(self, method_calls):
        payload = {
            "using": ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
            "methodCalls": method_calls
        }
        resp = self.session.post(self.api_url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def _fetch_details(self, email_ids):
        if not email_ids:
            return []
            
        get_call = [
            "Email/get",
            {
                "accountId": self.account_id,
                "ids": email_ids,
                # Added 'preview' to the properties list
                "properties": ["id", "subject", "from", "receivedAt", "preview", "textBody", "bodyValues"],
                "fetchTextBodyValues": True 
            },
            "1"
        ]
        response = self._call_jmap([get_call])
        return response['methodResponses'][0][1]['list']

    def extract_body_text(self, email_data):
        """Helper to stitch together the full plain text body."""
        text_content = ""
        parts = email_data.get('textBody', [])
        values = email_data.get('bodyValues', {})
        for part in parts:
            if part['partId'] in values:
                text_content += values[part['partId']]['value']
        return text_content

    def search_messages(self, keyword=None, subject_keyword=None, sender_keyword=None, since_dt=None, limit=20):
        filter_condition = {}
        
        if keyword:
            filter_condition["text"] = keyword
            
        if subject_keyword:
            filter_condition["subject"] = subject_keyword
            
        if sender_keyword:
            filter_condition["from"] = sender_keyword
            
        if since_dt:
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=timezone.utc)
            else:
                since_dt = since_dt.astimezone(timezone.utc)
            filter_condition["after"] = since_dt.strftime('%Y-%m-%dT%H:%M:%SZ')

        query_call = [
            "Email/query",
            {
                "accountId": self.account_id,
                "filter": filter_condition,
                "sort": [{"property": "receivedAt", "isAscending": False}],
                "limit": limit
            },
            "0"
        ]

        response = self._call_jmap([query_call])
        
        # Check for error responses
        if response['methodResponses'][0][0] == "error":
            raise ValueError(f"Search failed: {response['methodResponses'][0][1]}")

        email_ids = response['methodResponses'][0][1]['ids']
        return self._fetch_details(email_ids)

    def get_email_by_id(self, email_id: str):
        """
        Retrieve a single email by its ID.
        
        Args:
            email_id: The ID of the email to retrieve
            
        Returns:
            Email data dictionary or None if not found
        """
        emails = self._fetch_details([email_id])
        if emails:
            return emails[0]
        return None
    

# Bot function wrapper for integration with the assistant
class FastmailTool:
    """
    Function wrapper for FastmailClient to integrate with the bot's function calling system.
    """
    
    def __init__(self, token: Optional[str] = None):
        """
        Initialize the FastmailFunction.
        
        Args:
            token: Fastmail API token. If not provided, will use FASTMAIL_API_TOKEN env var.
        """
        if token is None:
            token = os.getenv("FASTMAIL_API_TOKEN")
        if not token:
            raise ValueError("Fastmail API token is required. Set FASTMAIL_API_TOKEN env var or pass token parameter.")
        self.fastmail_client = FastmailClient(token)
    
    @property
    def name(self):
        return "fastmail"
    
    @classproperty
    def toolname(cls):
        return "fastmail"
    
    @property
    def description(self):
        return """This function searches and retrieves email messages from a Fastmail account. It supports two operaions "find" and "get_email_body"
        The 'find' operation finds recent emails, using keyword search term and/or a time delta relative to present time, such as "1 day" or "2 hours" or "30 minutes".
        Both the keyword and timedelta are optional, and may be combined to find emails that match both criteria. 
        For instance, if keyword='soccer' and timedelta='1 day', the function will find all emails in the last day that contain the word 'soccer'.
        'find' returns basic information about the email including email id, sender, subject, received at, and a short preview.
        'find' also accepts a 'limit' parameter to specify the maximum number of emails to return. The default is 25.
        The 'get_email_body' operation retrieves the full body content of an email by its ID. The id can be found using the 'find' operation.
        """
    
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "The operation to perform",
                    "enum": ["find", "get_email_body"]
                },
                "timedelta": {
                    "type": "string",
                    "description": "Time period for finding emails before present (e.g., '1 day', '2 hours', '30 minutes'). Optional when operation is find, and may be combined with keyword search term."
                },
                "keyword": {
                    "type": "string",
                    "description": "Keyword to search for in emails, including body, subject, and sender. Optional when operation is 'find', and may be combined with timedelta."
                },
                "subject_keyword": {
                    "type": "string",
                    "description": "Text to search for in the subject line of emails. Optional when operation is 'find' and may be combined with other filters."
                },
                "sender_keyword": {
                    "type": "string",
                    "description": "Text to match against the sender address (From) of emails. Optional when operation is 'find' and may be combined with other filters."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of emails to return. Optional when operation is 'find', defaults to 25."
                },
                "email_id": {
                    "type": "string",
                    "description": "The ID of the email to retrieve. Required when operation is 'get_email_body'."
                }
            },
            "required": ["operation"]
        }
    
    def _parse_timedelta(self, td_string: str) -> timedelta:
        """
        Parse a timedelta string like "1 day", "2 hours", "30 minutes" into a timedelta object.
        
        Args:
            td_string: String like "1 day", "2 hours", "30 minutes"
            
        Returns:
            timedelta object
            
        Raises:
            ValueError: If the string cannot be parsed
        """
        td_string = td_string.strip().lower()
        
        # Pattern to match: number + unit (days, day, hours, hour, minutes, minute, etc.)
        pattern = r'(\d+)\s*(day|days|hour|hours|minute|minutes|second|seconds|week|weeks)'
        match = re.match(pattern, td_string)
        
        if not match:
            raise ValueError(f"Invalid timedelta format: '{td_string}'. Expected format like '1 day', '2 hours', '30 minutes'")
        
        value = int(match.group(1))
        unit = match.group(2).lower()
        
        # Normalize unit names
        if unit in ['day', 'days']:
            return timedelta(days=value)
        elif unit in ['hour', 'hours']:
            return timedelta(hours=value)
        elif unit in ['minute', 'minutes']:
            return timedelta(minutes=value)
        elif unit in ['second', 'seconds']:
            return timedelta(seconds=value)
        elif unit in ['week', 'weeks']:
            return timedelta(weeks=value)
        else:
            raise ValueError(f"Unknown time unit: '{unit}'")
    
    def _perform_search(
        self,
        keyword: Optional[str] = None,
        subject: Optional[str] = None,
        sender_keyword: Optional[str] = None,
        since_dt: Optional[datetime] = None,
        limit: int = 50,
    ):
        emails = self.fastmail_client.search_messages(
            keyword=keyword,
            subject_keyword=subject,
            sender_keyword=sender_keyword,
            since_dt=since_dt,
            limit=limit,
        )
        result = ""
        for email in emails:
            result += f"ID: {email.get('id')}\n"
            # Handle 'from' field which can be a list of objects with 'email' and 'name' fields
            from_field = email.get('from', [])
            if from_field and isinstance(from_field, list) and len(from_field) > 0:
                from_obj = from_field[0]
                from_email = from_obj.get('email', 'Unknown') if isinstance(from_obj, dict) else str(from_obj)
                result += f"From: {from_email}\n"
            else:
                result += f"From: Unknown\n"
            result += f"Email id: {email.get('id')}\n"
            result += f"Received at: {email.get('receivedAt', 'Unknown')}\n"
            result += f"Subject: {email.get('subject', 'No subject')}\n"
            result += f"Preview: {email.get('preview')}\n"
            result += "---\n"
            
        return result
        
    
    def __call__(self, **kwargs):
        operation = kwargs.get("operation")
        
        if not operation:
            return f"The function {self.name} requires an 'operation' keyword argument, but didn't get one"
        
        
        if operation == "find":
            td_string = kwargs.get("timedelta")
            keyword = kwargs.get("keyword")
            subject = kwargs.get("subject_keyword")
            sender_keyword = kwargs.get("sender_keyword")
            limit = kwargs.get("limit", 25)

            # Require at least one filtering criterion
            if not td_string and not keyword and not subject and not sender_keyword:
                return (
                    f"The function {self.name} with operation {operation} requires at least one of "
                    f"'timedelta', 'keyword', 'subject_keyword', or 'sender_keyword' keyword arguments, but didn't get any"
                )

            since_time = None
            if td_string:
                try:
                    td = self._parse_timedelta(td_string)
                    since_time = datetime.now(timezone.utc) - td
                except ValueError as e:
                    return f"Error parsing timedelta: {str(e)}"

            emails = self._perform_search(
                keyword=keyword,
                subject=subject,
                sender_keyword=sender_keyword,
                since_dt=since_time,
                limit=limit,
            )
            return emails
                
        elif operation == "get_email_body":
            email_id = kwargs.get("email_id")
            if not email_id:
                return f"The function {self.name} with operation {operation} requires an 'email_id' keyword argument, but didn't get one"
            
            email = self.fastmail_client.get_email_by_id(email_id)
            if not email:
                return f"Email with ID '{email_id}' not found"
            
            body_text = self.fastmail_client.extract_body_text(email)
            
            result = f"Email ID: {email.get('id')}\n"
            # Handle 'from' field which can be a list of objects with 'email' and 'name' fields
            from_field = email.get('from', [])
            if from_field and isinstance(from_field, list) and len(from_field) > 0:
                from_obj = from_field[0]
                from_email = from_obj.get('email', 'Unknown') if isinstance(from_obj, dict) else str(from_obj)
                result += f"From: {from_email}\n"
            else:
                result += f"From: Unknown\n"
            result += f"Subject: {email.get('subject', 'No subject')}\n"
            result += f"Received at: {email.get('receivedAt', 'Unknown')}\n"
            result += f"\n--- Body Content ---\n{body_text}\n"
            
            return result
        
        else:
            return f"Error: Unknown operation '{operation}'"
        


# --- Usage Example ---
if __name__ == "__main__":
    client = FastmailTool(os.getenv("FASTMAIL_API_TOKEN"))

    results = client(operation="find", keyword="youth program", sender_keyword="momentum", timedelta="300 days", limit=25)
    print(results)