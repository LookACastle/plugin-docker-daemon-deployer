import os
import re
from typing import Callable, Iterable

from lifecycle.config import Config
from lifecycle.monitor.base import JobMonitor
from lifecycle.monitor.health import check_until_job_is_operational, quick_check_job_condition
from lifecycle.monitor.metric_parser import read_last_call_timestamp_metric, scrape_metrics
from racetrack_client.log.exception import short_exception_details
from racetrack_client.utils.shell import shell_output
from racetrack_client.utils.time import datetime_to_timestamp, now
from racetrack_commons.deploy.resource import job_resource_name
from racetrack_commons.entities.dto import JobDto, JobStatus
from racetrack_client.log.logs import get_logger

from plugin_config import InfrastructureConfig

logger = get_logger(__name__)


class DockerDaemonMonitor(JobMonitor):
    """Discoverer listing job workloads deployed on a remote docker instance"""
    def __init__(self, infrastructure_target: str, infra_config: InfrastructureConfig) -> None:
        super().__init__()
        self.infra_config = infra_config
        self.infrastructure_target = infrastructure_target

    def list_jobs(self, config: Config) -> Iterable[JobDto]:
        # Heredoc to not mix quotes inside
        # Ports section needs to be last, because there were differences in outputs on developer systems:
        # One system had: 0.0.0.0:7020->7000/tcp
        # The other: 0.0.0.0:7000->7000/tcp, :::7000->7000/tcp
        cmd = f'DOCKER_HOST={self.infra_config.docker_host} ' + """
        docker ps -a --filter "name=^/job-" --format '{{.Names}} {{ .Label "job-name" }} {{ .Label "job-version" }} {{.Ports}}'
        """.strip()
        regex = r'(?P<resource_name>job-.+) (?P<job_name>.+) (?P<job_version>.+) 0\.0\.0\.0:(?P<job_port>\d+)->7000\/tcp'
        output = shell_output(cmd)

        assert self.infra_config.hostname, 'hostname of a docker daemon must be set'

        for line in output.splitlines():
            match = re.match(regex, line.strip())
            if match:
                resource_name = match.group('resource_name')
                job_name = match.group('job_name')
                job_version = match.group('job_version')
                job_port = match.group('job_port')

                internal_name = f'{self.infra_config.hostname}:{job_port}'

                job = JobDto(
                    name=job_name,
                    version=job_version,
                    status=JobStatus.RUNNING.value,
                    create_time=datetime_to_timestamp(now()),
                    update_time=datetime_to_timestamp(now()),
                    manifest=None,
                    internal_name=internal_name,
                    error=None,
                    infrastructure_target=self.infrastructure_target,
                )
                try:
                    job_url = f'http://{job.internal_name}'
                    quick_check_job_condition(job_url)
                    job_metrics = scrape_metrics(f'{job_url}/metrics')
                    job.last_call_time = read_last_call_timestamp_metric(job_metrics)
                except Exception as e:
                    error_details = short_exception_details(e)
                    job.error = error_details
                    job.status = JobStatus.ERROR.value
                    logger.warning(f'Job {job} is in bad condition: {error_details}')
                yield job

    def check_job_condition(self,
                               job: JobDto, 
                               deployment_timestamp: int = 0, 
                               on_job_alive: Callable = None,
                               logs_on_error: bool = True,
                               ):
        try:
            check_until_job_is_operational(f'http://{job.internal_name}', deployment_timestamp, on_job_alive)
        except Exception as e:
            if logs_on_error:
                logs = self.read_recent_logs(job)
                raise RuntimeError(f'{e}\nJob logs:\n{logs}')
            else:
                raise RuntimeError(str(e))

    def read_recent_logs(self, job: JobDto, tail: int = 20) -> str:
        container_name = job_resource_name(job.name, job.version)
        return shell_output(f'DOCKER_HOST={self.infra_config.docker_host} docker logs "{container_name}" --tail {tail}')
