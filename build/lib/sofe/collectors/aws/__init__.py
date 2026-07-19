"""AWS Collectors registry — all 18 collectors."""

from .ec2 import EC2Collector
from .s3 import S3Collector
from .lambda_ import LambdaCollector
from .rds import RDSCollector
from .ebs import EBSCollector
from .ecs import ECSCollector
from .eks import EKSCollector
from .elasticache import ElastiCacheCollector
from .redshift import RedshiftCollector
from .dynamodb import DynamoDBCollector
from .cloudfront import CloudFrontCollector
from .apigateway import APIGatewayCollector
from .natgateway import NATGatewayCollector
from .elb import ELBCollector
from .route53 import Route53Collector
from .secretsmanager import SecretsManagerCollector
from .sagemaker import SageMakerCollector
from .cost import CostCollector

# Registry: resource_type → Collector class
COLLECTORS = {
    "aws.ec2": EC2Collector,
    "aws.s3": S3Collector,
    "aws.lambda": LambdaCollector,
    "aws.rds": RDSCollector,
    "aws.ebs": EBSCollector,
    "aws.ecs": ECSCollector,
    "aws.eks": EKSCollector,
    "aws.elasticache": ElastiCacheCollector,
    "aws.redshift": RedshiftCollector,
    "aws.dynamodb": DynamoDBCollector,
    "aws.cloudfront": CloudFrontCollector,
    "aws.apigateway": APIGatewayCollector,
    "aws.natgateway": NATGatewayCollector,
    "aws.elb": ELBCollector,
    "aws.route53": Route53Collector,
    "aws.secretsmanager": SecretsManagerCollector,
    "aws.sagemaker": SageMakerCollector,
    "aws.cost": CostCollector,
}

ALL_TYPES = list(COLLECTORS.keys())
