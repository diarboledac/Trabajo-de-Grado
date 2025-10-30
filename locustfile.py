from locust import HttpUser, task, between
import os

TARGET_PATH = os.getenv("TARGET_PATH", "/")

class DeviceUser(HttpUser):
    wait_time = between(0.5, 1.5)

    @task
    def ping(self):
        self.client.get(TARGET_PATH, name="ping")
