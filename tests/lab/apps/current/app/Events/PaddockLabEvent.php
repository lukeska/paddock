<?php
namespace App\Events;
use Illuminate\Broadcasting\Channel;
use Illuminate\Contracts\Broadcasting\ShouldBroadcastNow;
use Illuminate\Foundation\Events\Dispatchable;
use Illuminate\Queue\SerializesModels;
class PaddockLabEvent implements ShouldBroadcastNow {
    use Dispatchable, SerializesModels;
    public function __construct(public string $token) {}
    public function broadcastOn(): array { return [new Channel('paddock-lab')]; }
    public function broadcastAs(): string { return 'PaddockLabEvent'; }
}
