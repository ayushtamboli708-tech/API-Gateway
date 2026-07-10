import redis.asyncio as redis

# Initialize the asynchronous Redis client
# decode_responses=True ensures we get Python strings back instead of raw bytes
redis_client = redis.Redis(
    host="localhost", 
    port=6379, 
    decode_responses=True
)