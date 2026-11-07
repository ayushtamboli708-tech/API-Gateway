from locust import HttpUser, between, task


class GatewayUser(HttpUser):
    # Reduced wait time to push more concurrent requests
    wait_time = between(0.01, 0.1)

    @task
    def hit_gateway(self):
        self.client.get(
            url="/api/v1/users/test",
            headers={"x-api-key": "key-loadtest-999"}
        )