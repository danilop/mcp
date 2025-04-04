"""awslabs lambda MCP Server implementation."""

import argparse
import boto3
import json
import logging
import os
import re
from mcp.server.fastmcp import Context, FastMCP


# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWS_PROFILE = os.environ.get('AWS_PROFILE', 'default')
logger.info(f'AWS_PROFILE: {AWS_PROFILE}')
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')
logger.info(f'AWS_REGION: {AWS_REGION}')
FUNCTION_PREFIX = os.environ.get('FUNCTION_PREFIX', 'mcp2lambda-')
logger.info(f'FUNCTION_PREFIX: {FUNCTION_PREFIX}')
FUNCTION_LIST = [
    function_name.strip()
    for function_name in os.environ.get('FUNCTION_LIST', '').split(',')
    if function_name.strip()
]
logger.info(f'FUNCTION_LIST: {FUNCTION_LIST}')

lambda_client = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION).client('lambda')

mcp = FastMCP(
    'awslabs.lambda-mcp-server',
    instructions="""Use AWS Lambda functions to improve your answers.
    These Lambda functions can give you additional capabilities and access to AWS services and resources in an AWS account.""",
    dependencies=['pydantic', 'boto3'],
)


def validate_function_name(function_name: str) -> bool:
    """Validate that the function name is valid and can be called."""
    return function_name.startswith(FUNCTION_PREFIX) or function_name in FUNCTION_LIST


def sanitize_tool_name(name: str) -> str:
    """Sanitize a Lambda function name to be used as a tool name."""
    # Remove prefix if present
    if name.startswith(FUNCTION_PREFIX):
        name = name[len(FUNCTION_PREFIX) :]

    # Replace invalid characters with underscore
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)

    # Ensure name doesn't start with a number
    if name and name[0].isdigit():
        name = '_' + name

    return name


def format_lambda_response(function_name: str, payload: bytes) -> str:
    """Format the Lambda function response payload."""
    try:
        # Try to parse the payload as JSON
        payload_json = json.loads(payload)
        return f'Function {function_name} returned: {json.dumps(payload_json, indent=2)}'
    except (json.JSONDecodeError, UnicodeDecodeError):
        # Return raw payload if not JSON
        return f'Function {function_name} returned payload: {payload}'


def invoke_lambda_function_impl(function_name: str, parameters: dict, ctx: Context) -> str:
    """Tool that invokes an AWS Lambda function with a JSON payload."""
    ctx.info(f'Invoking {function_name} with parameters: {parameters}')

    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType='RequestResponse',
        Payload=json.dumps(parameters),
    )

    ctx.info(f'Function {function_name} returned with status code: {response["StatusCode"]}')

    if 'FunctionError' in response:
        error_message = (
            f'Function {function_name} returned with error: {response["FunctionError"]}'
        )
        ctx.error(error_message)
        return error_message

    payload = response['Payload'].read()
    # Format the response payload
    return format_lambda_response(function_name, payload)


def create_lambda_tool(function_name: str, description: str):
    """Create a tool function for a Lambda function."""
    # Create a meaningful tool name
    tool_name = sanitize_tool_name(function_name)

    # Define the inner function
    def lambda_function(parameters: dict, ctx: Context) -> str:
        """Tool for invoking a specific AWS Lambda function with parameters."""
        # Use the same implementation as the generic invoke function
        return invoke_lambda_function_impl(function_name, parameters, ctx)

    # Set the function's documentation
    lambda_function.__doc__ = description

    logger.info(f'Registering tool {tool_name} with description: {description}')
    # Apply the decorator manually with the specific name
    decorated_function = mcp.tool(name=tool_name)(lambda_function)

    return decorated_function


# Register Lambda functions as individual tools if dynamic strategy is enabled
def register_lambda_functions():
    """Register Lambda functions as individual tools."""
    try:
        logger.info('Registering Lambda functions as individual tools...')
        functions = lambda_client.list_functions()
        valid_functions = [
            f for f in functions['Functions'] if validate_function_name(f['FunctionName'])
        ]

        logger.info(f'{len(valid_functions)} Lambda functions found.')

        for function in valid_functions:
            function_name = function['FunctionName']
            description = function.get('Description', f'AWS Lambda function: {function_name}')

            create_lambda_tool(function_name, description)

        logger.info('Lambda functions registered successfully as individual tools.')

    except Exception as e:
        logger.error(f'Error registering Lambda functions as tools: {e}')


def main():
    """Run the MCP server with CLI argument support."""
    parser = argparse.ArgumentParser(
        description='An AWS Labs Model Context Protocol (MCP) server for Lambda'
    )
    parser.add_argument('--sse', action='store_true', help='Use SSE transport')
    parser.add_argument('--port', type=int, default=8888, help='Port to run the server on')

    args = parser.parse_args()

    register_lambda_functions()

    # Run server with appropriate transport
    if args.sse:
        mcp.settings.port = args.port
        mcp.run(transport='sse')
    else:
        mcp.run()


if __name__ == '__main__':
    main()
