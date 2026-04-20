#!/bin/bash

# Script to set up a local Nextcloud test environment with required apps

echo "🚀 Starting Nextcloud test environment..."

# Start Docker Compose
docker compose up -d

echo "⏳ Waiting for Nextcloud to be ready..."
while ! curl -s http://localhost:8080/status.php | grep -q '"installed":true'; do
    sleep 2
done

echo "✅ Nextcloud is ready!"

# Enable required apps
echo "🔧 Enabling required Nextcloud apps..."
docker exec nextcloud-test php occ app:enable groupfolders
docker exec nextcloud-test php occ app:enable circles
docker exec nextcloud-test php occ app:enable collectives
docker exec nextcloud-test php occ app:enable spreed

# Create anchor user
echo "Creating anchor_user..."
docker exec --env NC_PASS=anchoruser123 nextcloud-test php occ user:add anchor_user --password-from-env --group=admin

# Create test users
echo "Creating test users..."
for i in {1..10}; do
    docker exec --env NC_PASS='testMe!1234567' nextcloud-test php occ user:add "test$i" --password-from-env
done

echo ""
echo "✅ Test environment is ready!"
echo ""
echo "📝 Configuration for test-environment:"
echo "   NC_URL=http://localhost:8080"
echo "   NC_ANCHOR_USER=anchor_user"
echo "   NC_ANCHOR_APP_PW=anchoruser123"
echo ""
echo "🌐 Access Nextcloud at: http://localhost:8080"
echo "👤 Admin credentials: admin / admin123"
echo "👤 Anchor credentials: anchor_user / anchoruser123"
echo "👤 Test users: test1-test10 / testMe!1234567"
echo ""
echo "To stop the environment: docker-compose down"
echo "To view logs: docker-compose logs -f"
