import mysql from './assets/resource-icons/mysql.svg';
import postgresql from './assets/resource-icons/postgresql.svg';
import linux from './assets/resource-icons/linux.svg';
import kubernetes from './assets/resource-icons/kubernetes.svg';
import windows from './assets/resource-icons/windows11.svg';
import mssql from './assets/resource-icons/microsoftsqlserver.svg';
import redis from './assets/resource-icons/redis.svg';
import mongodb from './assets/resource-icons/mongodb.svg';
import clickhouse from './assets/resource-icons/clickhouse.svg';

const products: Record<string, { name: string; icon: string; alt: string }> = {
  mysql: { name: 'MySQL', icon: mysql, alt: 'MySQL' },
  postgresql: { name: 'PostgreSQL', icon: postgresql, alt: 'PostgreSQL' },
  ssh: { name: 'Linux / SSH', icon: linux, alt: 'Linux' },
  k8s: { name: 'Kubernetes', icon: kubernetes, alt: 'Kubernetes' },
  rdp: { name: 'Windows / RDP', icon: windows, alt: 'Windows' },
  mssql: { name: 'SQL Server', icon: mssql, alt: 'SQL Server' },
  redis: { name: 'Redis', icon: redis, alt: 'Redis' },
  mongodb: { name: 'MongoDB', icon: mongodb, alt: 'MongoDB' },
  clickhouse: { name: 'ClickHouse', icon: clickhouse, alt: 'ClickHouse' },
};

export function resourceProduct(type: string) {
  return products[type] || { name: type || '资源', icon: '', alt: '' };
}

export function environmentLabel(value?: string) {
  if (!value) return '未分配环境';
  return ({ prod: '生产环境', production: '生产环境', uat: '验收环境', staging: '预发布环境', test: '测试环境', dev: '开发环境' } as Record<string, string>)[value] || value;
}
