"""Table definitions and creation helper for DynamoDB persistence."""
import os
import boto3

def get_table_name(base_name: str, prefix: str | None = None) -> str:
    if prefix is None:
        prefix = os.environ.get("DYNAMO_TABLE_PREFIX", "")
    return f"{prefix}{base_name}"

def create_tables(dynamodb, prefix: str | None = None):
    """Create all 8 tables if they do not already exist."""
    existing = set()
    try:
        # If resource or client passed
        client = dynamodb.meta.client if hasattr(dynamodb, "meta") else dynamodb
        existing = set(client.list_tables().get("TableNames", []))
    except Exception:
        pass

    definitions = [
        {
            "TableName": get_table_name("Business", prefix),
            "KeySchema": [{"AttributeName": "business_id", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "business_id", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": get_table_name("Slots", prefix),
            "KeySchema": [
                {"AttributeName": "slot_pk", "KeyType": "HASH"},
                {"AttributeName": "start_unit", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "slot_pk", "AttributeType": "S"},
                {"AttributeName": "start_unit", "AttributeType": "N"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": get_table_name("Bookings", prefix),
            "KeySchema": [
                {"AttributeName": "customer_id", "KeyType": "HASH"},
                {"AttributeName": "start_time_booking_id", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "customer_id", "AttributeType": "S"},
                {"AttributeName": "start_time_booking_id", "AttributeType": "S"},
                {"AttributeName": "business_id", "AttributeType": "S"},
                {"AttributeName": "date", "AttributeType": "S"},
            ],
            "GlobalSecondaryIndexes": [
                {
                    "IndexName": "by_business_date",
                    "KeySchema": [
                        {"AttributeName": "business_id", "KeyType": "HASH"},
                        {"AttributeName": "date", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": get_table_name("Waitlist", prefix),
            "KeySchema": [
                {"AttributeName": "business_service", "KeyType": "HASH"},
                {"AttributeName": "joined_customer", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "business_service", "AttributeType": "S"},
                {"AttributeName": "joined_customer", "AttributeType": "S"},
            ],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": get_table_name("Idempotency", prefix),
            "KeySchema": [{"AttributeName": "idempotency_key", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "idempotency_key", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": get_table_name("Events", prefix),
            "KeySchema": [
                {"AttributeName": "business_id", "KeyType": "HASH"},
                {"AttributeName": "timestamp_event_id", "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": [
                {"AttributeName": "business_id", "AttributeType": "S"},
                {"AttributeName": "timestamp_event_id", "AttributeType": "S"},
            ],
            "StreamSpecification": {
                "StreamEnabled": True,
                "StreamViewType": "NEW_IMAGE",
            },
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": get_table_name("OAuthCodes", prefix),
            "KeySchema": [{"AttributeName": "code_hash", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "code_hash", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
        {
            "TableName": get_table_name("OAuthTokens", prefix),
            "KeySchema": [{"AttributeName": "token_hash", "KeyType": "HASH"}],
            "AttributeDefinitions": [{"AttributeName": "token_hash", "AttributeType": "S"}],
            "BillingMode": "PAY_PER_REQUEST",
        },
    ]

    client = dynamodb.meta.client if hasattr(dynamodb, "meta") else dynamodb
    for d in definitions:
        if d["TableName"] not in existing:
            client.create_table(**d)
            existing.add(d["TableName"])
