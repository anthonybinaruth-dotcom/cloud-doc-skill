"""定时任务调度器模块"""

import asyncio
import logging
from datetime import datetime
from typing import Callable, Optional

from croniter import croniter
import pytz


class Scheduler:
    """基于 cron 表达式的任务调度器"""

    def __init__(
        self,
        cron_expr: str = "0 10 * * *",
        timezone: str = "Asia/Shanghai",
        job_name: str = "scheduled_job",
    ):
        """
        初始化调度器
        
        Args:
            cron_expr: cron 表达式，如 "15 11 * * *" 表示每天 11:15
            timezone: 时区，默认 Asia/Shanghai
            job_name: 任务名称，用于日志
        """
        self.cron_expr = cron_expr
        self.timezone = pytz.timezone(timezone)
        self.job_name = job_name
        self._running = False
        self._task: Optional[asyncio.Task] = None
        
    def _get_next_run_time(self) -> datetime:
        """计算下次运行时间"""
        now = datetime.now(self.timezone)
        cron = croniter(self.cron_expr, now)
        return cron.get_next(datetime)
    
    def _seconds_until_next_run(self) -> float:
        """计算距离下次运行的秒数"""
        next_run = self._get_next_run_time()
        now = datetime.now(self.timezone)
        delta = next_run - now
        return max(0, delta.total_seconds())
    
    async def run_once(self, job: Callable) -> None:
        """立即执行一次任务"""
        logging.info(f"[{self.job_name}] 开始执行...")
        start_time = datetime.now()
        try:
            if asyncio.iscoroutinefunction(job):
                await job()
            else:
                job()
            elapsed = (datetime.now() - start_time).total_seconds()
            logging.info(f"[{self.job_name}] 执行完成，耗时 {elapsed:.1f}s")
        except Exception as e:
            logging.error(f"[{self.job_name}] 执行失败: {e}", exc_info=True)
    
    async def start(self, job: Callable) -> None:
        """启动定时任务循环"""
        self._running = True
        logging.info(
            f"[{self.job_name}] 定时任务已启动\n"
            f"  - cron: {self.cron_expr}\n"
            f"  - timezone: {self.timezone}\n"
            f"  - 下次执行: {self._get_next_run_time()}"
        )
        
        while self._running:
            sleep_seconds = self._seconds_until_next_run()
            logging.info(f"[{self.job_name}] 等待 {sleep_seconds:.0f}s 后执行下次任务...")
            
            # 分段睡眠，以便能够响应停止信号
            while sleep_seconds > 0 and self._running:
                sleep_time = min(60, sleep_seconds)  # 每次最多睡眠 60s
                await asyncio.sleep(sleep_time)
                sleep_seconds -= sleep_time
            
            if not self._running:
                break
                
            await self.run_once(job)
            
            # 等待一小段时间避免重复执行
            await asyncio.sleep(1)
    
    def stop(self) -> None:
        """停止定时任务"""
        self._running = False
        logging.info(f"[{self.job_name}] 定时任务已停止")


class MultiScheduler:
    """多任务调度器，支持同时运行多个定时任务"""
    
    def __init__(self):
        self.schedulers: list[tuple[Scheduler, Callable]] = []
        self._running = False
    
    def add_job(
        self,
        job: Callable,
        cron_expr: str,
        timezone: str = "Asia/Shanghai",
        job_name: str = "job",
    ) -> None:
        """添加一个定时任务"""
        scheduler = Scheduler(cron_expr, timezone, job_name)
        self.schedulers.append((scheduler, job))
    
    async def start_all(self) -> None:
        """启动所有定时任务"""
        self._running = True
        tasks = []
        for scheduler, job in self.schedulers:
            task = asyncio.create_task(scheduler.start(job))
            tasks.append(task)
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logging.info("所有定时任务已取消")
    
    def stop_all(self) -> None:
        """停止所有定时任务"""
        self._running = False
        for scheduler, _ in self.schedulers:
            scheduler.stop()