bucket         = "civiclens-terraform-state"
key            = "staging/terraform.tfstate"
region         = "us-east-1"
dynamodb_table = "civiclens-terraform-lock"
encrypt        = true
