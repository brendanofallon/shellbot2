
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path

from gcsa.google_calendar import GoogleCalendar
from gcsa.event import Event
from google.oauth2.service_account import Credentials

from v3shellbot.tools.util import classproperty

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']
PERSONAL_CALENDAR_ID = 'brendanofallon@gmail.com'
OFINDFORS_CALENDAR_ID = 'f2qpbfbfn58tsftgo07nr96rt0@group.calendar.google.com'


class Calendar:
    """
    An interface for reading and creating events in Google Calendar.
    
    Provides access to events from two calendars:
    - 'personal': Personal calendar (read and write)
    - 'ofindfors': Ofindfors calendar (read-only)
    """
    
    CALENDAR_IDS = {
        'personal': PERSONAL_CALENDAR_ID,
        'ofindfors': OFINDFORS_CALENDAR_ID
    }
    
    def __init__(self, credentials_file: Optional[str] = None):
        """
        Initialize the CalendarTool.
        
        Args:
            credentials_file: Path to Google service account credentials JSON file.
                            If None, reads from GOOGLE_CALENDAR_CREDENTIALS_FILE env var.
        """
        if credentials_file is None:
            credentials_file = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE")
        
        if not credentials_file:
            raise ValueError(
                "Credentials file path must be provided either as argument or "
                "via GOOGLE_CALENDAR_CREDENTIALS_FILE environment variable"
            )
        
        if not os.path.exists(credentials_file):
            raise FileNotFoundError(f"Credentials file not found: {credentials_file}")
        
        self.credentials_file = Path(credentials_file)
        self.creds = Credentials.from_service_account_file(
            str(self.credentials_file), scopes=SCOPES
        )
        logger.info(f"CalendarTool initialized with credentials file: {self.credentials_file}")
    
    def _get_calendar_id(self, calendar_name: str) -> str:
        """
        Get the calendar ID for a given calendar name.
        
        Args:
            calendar_name: Name of the calendar ('personal' or 'ofindfors')
        
        Returns:
            The calendar ID
        
        Raises:
            ValueError: If calendar_name is not recognized
        """
        if calendar_name not in self.CALENDAR_IDS:
            raise ValueError(
                f"Unknown calendar '{calendar_name}'. "
                f"Available calendars: {list(self.CALENDAR_IDS.keys())}"
            )
        return self.CALENDAR_IDS[calendar_name]
    
    def get_events(
        self,
        calendar_name: str,
        time_min: Optional[datetime] = None,
        time_max: Optional[datetime] = None,
        max_results: Optional[int] = None
    ) -> List[Dict]:
        """
        Retrieve events from a calendar within a time range.
        
        Args:
            calendar_name: Name of the calendar ('personal' or 'ofindfors')
            time_min: Start time for the query (defaults to now)
            time_max: End time for the query (defaults to 7 days from now)
            max_results: Maximum number of events to return (optional)
        
        Returns:
            List of event dictionaries with keys: summary, start, end, description, location
        """
        try:
            calendar_id = self._get_calendar_id(calendar_name)
            calendar = GoogleCalendar(calendar_id, credentials=self.creds)
            
            # Default time_min to now if not provided
            if time_min is None:
                time_min = datetime.now()
            
            # Default time_max to 7 days from time_min if not provided
            if time_max is None:
                time_max = time_min + timedelta(days=7)
            
            # Get events
            events = calendar.get_events(time_min=time_min, time_max=time_max)
            
            # Convert to list of dictionaries
            event_list = []
            for event in events:
                event_dict = {
                    'summary': event.summary or '(No title)',
                    'start': str(event.start) if event.start else None,
                    'end': str(event.end) if event.end else None,
                    'description': event.description or '',
                    'location': event.location or ''
                }
                event_list.append(event_dict)
                
                # Limit results if max_results is specified
                if max_results and len(event_list) >= max_results:
                    break
            
            logger.info(
                f"Retrieved {len(event_list)} events from calendar '{calendar_name}' "
                f"between {time_min} and {time_max}"
            )
            return event_list
            
        except Exception as e:
            logger.error(f"Error retrieving events from calendar '{calendar_name}': {e}")
            raise
    
    def create_event(
        self,
        calendar_name: str,
        summary: str,
        start: datetime,
        end: datetime,
        description: Optional[str] = None,
        location: Optional[str] = None
    ) -> Dict:
        """
        Create a new event in the specified calendar.
        
        Args:
            calendar_name: Name of the calendar ('personal' only for now)
            summary: Title of the event
            start: Start time of the event
            end: End time of the event
            description: Optional description of the event
            location: Optional location of the event
        
        Returns:
            Dictionary with event details including the created event ID
        
        Raises:
            ValueError: If calendar_name is not 'personal' (only personal calendar supports writes)
        """
        if calendar_name != 'personal':
            raise ValueError(
                f"Event creation is only supported for the 'personal' calendar. "
                f"Received: '{calendar_name}'"
            )
        
        try:
            calendar_id = self._get_calendar_id(calendar_name)
            calendar = GoogleCalendar(calendar_id, credentials=self.creds)
            
            # Create event object
            event = Event(
                summary=summary,
                start=start,
                end=end,
                description=description or '',
                location=location or ''
            )
            
            # Add event to calendar
            created_event = calendar.add_event(event)
            
            logger.info(
                f"Created event '{summary}' in calendar '{calendar_name}' "
                f"from {start} to {end}"
            )
            
            return {
                'id': created_event.id if hasattr(created_event, 'id') else None,
                'summary': created_event.summary or summary,
                'start': str(created_event.start) if created_event.start else str(start),
                'end': str(created_event.end) if created_event.end else str(end),
                'description': created_event.description or description or '',
                'location': created_event.location or location or ''
            }
            
        except Exception as e:
            logger.error(f"Error creating event in calendar '{calendar_name}': {e}")
            raise


class CalendarTool:
    """
    Function wrapper for CalendarTool to integrate with the bot's function calling system.
    """
    
    def __init__(self, credentials_file: Optional[str] = None):
        self.calendar_tool = Calendar(credentials_file)
    
    @property
    def name(self):
        return "calendar"
    
    @classproperty
    def toolname(cls):
        return "calendar"
    
    @property
    def description(self):
        return """This function provides access to Google Calendar events, events can be created or retrieved in a specified time range.  
        The 'operation' parameter is required and must be either get_events or create_event.
        The 'calendar' parameter is required and must be one of: personal or ofindfors.
        For create_event, only 'personal' calendar is supported.
        
        The two calendars are:
        - 'personal': Personal calendar (read and write)
        - 'ofindfors': O'Findfors family calendar (read-only)
        
        Operations:
        - 'get_events': Retrieves events within a specified time range.
          Time parameters must be provided in ISO 8601 format (YYYY-MM-DDTHH:MM:SS).
          If time_min is not provided, it defaults to the current time.
          If time_max is not provided, it defaults to 7 days after time_min.
        
        - 'create_event': Creates a new event in the 'personal' calendar.
          Requires summary, start, and end times in ISO 8601 format.
          Optional parameters: description, location.
        """
    
    @property
    def parameters(self):
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "The operation to perform, must be one of: get_events, create_event",
                    "enum": ["get_events", "create_event"]
                },
                "calendar": {
                    "type": "string",
                    "description": "The calendar to operate on, must be either 'personal' or 'ofindfors'. For create_event, only 'personal' is supported.",
                    "enum": ["personal", "ofindfors"]
                },
                "time_min": {
                    "type": "string",
                    "description": "Start time for the query in ISO 8601 format (YYYY-MM-DDTHH:MM:SS). Used for get_events. Defaults to current time if not provided."
                },
                "time_max": {
                    "type": "string",
                    "description": "End time for the query in ISO 8601 format (YYYY-MM-DDTHH:MM:SS). Used for get_events. Defaults to 7 days after time_min if not provided."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of events to return (optional, for get_events only)"
                },
                "summary": {
                    "type": "string",
                    "description": "Title of the event (required for create_event)"
                },
                "start": {
                    "type": "string",
                    "description": "Start time of the event in ISO 8601 format (YYYY-MM-DDTHH:MM:SS) (required for create_event)"
                },
                "end": {
                    "type": "string",
                    "description": "End time of the event in ISO 8601 format (YYYY-MM-DDTHH:MM:SS) (required for create_event)"
                },
                "description": {
                    "type": "string",
                    "description": "Description of the event (optional, for create_event)"
                },
                "location": {
                    "type": "string",
                    "description": "Location of the event (optional, for create_event)"
                }
            },
            "required": ["operation", "calendar"]
        }
    
    def _parse_datetime(self, time_str: str) -> datetime:
        """
        Parse a datetime string in ISO 8601 format.
        
        Args:
            time_str: Datetime string in ISO 8601 format (YYYY-MM-DDTHH:MM:SS)
        
        Returns:
            Parsed datetime object
        
        Raises:
            ValueError: If the string is not in valid ISO 8601 format
        """
        
        # Handle with or without timezone
        if 'T' in time_str:
            # Try parsing with timezone
            try:
                return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            except ValueError:
                # Try without timezone
                return datetime.fromisoformat(time_str)
        else:
            # Date only, assume start of day
            return datetime.fromisoformat(time_str)
        
    
    def __call__(self, **kwargs):
        operation = kwargs.get("operation")
        calendar = kwargs.get("calendar")
        assert operation in ["get_events", "create_event"], f"Invalid operation: {operation}, operation must be one of: get_events, create_event"
        assert calendar in ["personal", "ofindfors"], f"Invalid calendar: {calendar}, calendar must be one of: personal, ofindfors"
        
        if operation == "get_events":
            if not calendar:
                return "Error: 'calendar' parameter is required for get_events operation"
            
            # Parse time parameters
            time_min = None
            if kwargs.get("time_min"):
                time_min = self._parse_datetime(kwargs["time_min"])
            
            time_max = None
            if kwargs.get("time_max"):
                time_max = self._parse_datetime(kwargs["time_max"])
            
            max_results = kwargs.get("max_results")
            
            events = self.calendar_tool.get_events(
                calendar_name=calendar,
                time_min=time_min,
                time_max=time_max,
                max_results=max_results
            )
            
            if not events:
                return f"No events found in calendar '{calendar}' for the specified time range."
            
            # Format events for display
            result_lines = [f"Found {len(events)} event(s) in calendar '{calendar}':\n"]
            for i, event in enumerate(events, 1):
                result_lines.append(f"{i}. {event['summary']}")
                if event['start']:
                    result_lines.append(f"   Start: {event['start']}")
                if event['end']:
                    result_lines.append(f"   End: {event['end']}")
                if event['location']:
                    result_lines.append(f"   Location: {event['location']}")
                if event['description']:
                    # Truncate long descriptions
                    desc = event['description']
                    if len(desc) > 200:
                        desc = desc[:200] + "..."
                    result_lines.append(f"   Description: {desc}")
                result_lines.append("")
            
            return "\n".join(result_lines)
        
        elif operation == "create_event":
            if not calendar:
                return "Error: 'calendar' parameter is required for create_event operation"
            
            if calendar != 'personal':
                return f"Error: Event creation is only supported for the 'personal' calendar. Received: '{calendar}'"
            
            # Validate required parameters
            summary = kwargs.get("summary")
            if not summary:
                return "Error: 'summary' parameter is required for create_event operation"
            
            start_str = kwargs.get("start")
            if not start_str:
                return "Error: 'start' parameter is required for create_event operation"
            
            end_str = kwargs.get("end")
            if not end_str:
                return "Error: 'end' parameter is required for create_event operation"
            
            # Parse datetime parameters
            try:
                start = self._parse_datetime(start_str)
            except ValueError as e:
                return f"Error: Invalid start time format: {e}"
            
            try:
                end = self._parse_datetime(end_str)
            except ValueError as e:
                return f"Error: Invalid end time format: {e}"
            
            # Validate that end is after start
            if end <= start:
                return "Error: End time must be after start time"
            
            # Optional parameters
            description = kwargs.get("description")
            location = kwargs.get("location")
            
            try:
                created_event = self.calendar_tool.create_event(
                    calendar_name=calendar,
                    summary=summary,
                    start=start,
                    end=end,
                    description=description,
                    location=location
                )
                
                result_lines = [f"Successfully created event in calendar '{calendar}':\n"]
                result_lines.append(f"Title: {created_event['summary']}")
                result_lines.append(f"Start: {created_event['start']}")
                result_lines.append(f"End: {created_event['end']}")
                if created_event['location']:
                    result_lines.append(f"Location: {created_event['location']}")
                if created_event['description']:
                    result_lines.append(f"Description: {created_event['description']}")
                if created_event.get('id'):
                    result_lines.append(f"Event ID: {created_event['id']}")
                
                return "\n".join(result_lines)
                
            except ValueError as e:
                return f"Error: {e}"
            except Exception as e:
                logger.error(f"Error creating event: {e}")
                return f"Error creating event: {e}"
        
        else:
            return f"Error: Unknown operation '{operation}'"


if __name__ == "__main__":
    # Example usage
    import sys
    
    if len(sys.argv) > 1:
        creds_file = sys.argv[1]
    else:
        creds_file = os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE")

    
    tool = CalendarTool(credentials_file=creds_file)
    
    result = tool(operation='create_event',
        calendar='personal', 
        summary='Test Event', 
        start=(datetime.now() + timedelta(hours=1)).isoformat(), end=(datetime.now() + timedelta(hours=2)).isoformat()  
    )
    print(result)

    # Get events from personal calendar for next day
    print("Personal calendar events (next 24 hours):")
    events = tool.calendar_tool.get_events('personal', time_max=datetime.now() + timedelta(days=1))
    for event in events:
        print(f"  {event['start']} - {event['summary']}")
    