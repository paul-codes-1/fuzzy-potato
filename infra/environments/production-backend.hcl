bucket         = "civiclens-terraform-state"
key            = "production/terraform.tfstate"
region         = "us-east-1"
dynamodb_table = "civiclens-terraform-lock"
encrypt        = true
